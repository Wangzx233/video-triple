#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
手术视频标注系统 - 后端API
支持视频三元组标注（器械、动作、目标）
"""

import os
import sys
import json
import uuid
import csv
import copy
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime
from flask import Flask, render_template, jsonify, request, send_from_directory, Response
from flask_cors import CORS
import cv2
import subprocess
import tempfile
import threading
from io import BytesIO

APP_NAME = '视频标注系统'
DEFAULT_TRIPLET_OPTIONS = {
    'instruments': ['grasper', 'scissors', 'forceps', 'scalpel', 'needle_holder'],
    'actions': ['grasp', 'cut', 'dissect', 'suture', 'clip'],
    'targets': ['tissue', 'vessel', 'organ', 'tumor', 'bleeding_point']
}
DEFAULT_APP_CONFIG = {
    'triplet_fields': {
        'instrument': {
            'label': '器械',
            'select_placeholder': '选择器械...',
            'custom_placeholder': '输入自定义器械名称'
        },
        'action': {
            'label': '动作',
            'select_placeholder': '选择动作...',
            'custom_placeholder': '输入自定义动作名称'
        },
        'target': {
            'label': '目标',
            'select_placeholder': '选择目标...',
            'custom_placeholder': '输入自定义目标名称'
        }
    },
    'triplet_customization': {
        'allow_custom_input': True,
        'custom_option_label': '🖊️ 自定义输入'
    }
}


def get_data_root():
    """获取数据目录，兼容开发和打包环境"""
    if getattr(sys, 'frozen', False):
        return os.path.join(os.path.dirname(sys.executable), 'data')
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')


def resolve_path_under_root(root, relative_path):
    """Resolve a user-facing relative path and keep it inside the given root."""
    normalized = os.path.normpath(relative_path.replace('\\', '/'))
    candidate = os.path.abspath(os.path.join(root, normalized))
    root_abs = os.path.abspath(root)
    if os.path.commonpath([root_abs, candidate]) != root_abs:
        raise ValueError(f'Invalid path: {relative_path}')
    return candidate


def get_video_file_path(video_name):
    """获取视频文件绝对路径"""
    return resolve_path_under_root(VIDEOS_FOLDER, video_name)


def get_app_config_path():
    """获取应用配置文件路径"""
    return os.path.join(DATA_FOLDER, 'app_config.json')

# 获取模板和静态文件路径
def resolve_resource_path(subdir):
    """Resolve resource paths for dev/onedir/onefile."""
    candidates = []
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            candidates.append(os.path.join(meipass, subdir))
        candidates.append(os.path.join(os.path.dirname(sys.executable), '_internal', subdir))
        candidates.append(os.path.join(os.path.dirname(sys.executable), subdir))
    else:
        candidates.append(os.path.join(os.path.dirname(os.path.abspath(__file__)), subdir))

    for path in candidates:
        if os.path.isdir(path):
            return path
    return candidates[0]

def get_template_path():
    """获取模板文件路径"""
    if getattr(sys, 'frozen', False):
        # PyInstaller环境：优先使用_MEIPASS（onefile），否则使用_internal（onedir）
        return resolve_resource_path('templates')
    else:
        # 开发环境
        return resolve_resource_path('templates')

def get_static_path():
    """获取静态文件路径"""
    if getattr(sys, 'frozen', False):
        # PyInstaller环境：优先使用_MEIPASS（onefile），否则使用_internal（onedir）
        return resolve_resource_path('static')
    else:
        # 开发环境
        return resolve_resource_path('static')

template_folder = get_template_path()
static_folder = get_static_path()

app = Flask(__name__, 
           template_folder=template_folder,
           static_folder=static_folder)
CORS(app)

# 配置
DATA_FOLDER = get_data_root()
VIDEOS_FOLDER = os.path.join(DATA_FOLDER, 'videos')
ANNOTATIONS_FOLDER = os.path.join(DATA_FOLDER, 'annotations')
STATIC_FOLDER = static_folder
TEMPLATES_FOLDER = template_folder

# 确保必要的文件夹存在
folders_to_create = [DATA_FOLDER, VIDEOS_FOLDER, ANNOTATIONS_FOLDER]
if not getattr(sys, 'frozen', False):
    folders_to_create.extend([STATIC_FOLDER, TEMPLATES_FOLDER])

for folder in folders_to_create:
    os.makedirs(folder, exist_ok=True)


def deep_merge_dict(base, overrides):
    """递归合并配置字典"""
    merged = copy.deepcopy(base)
    if not isinstance(overrides, dict):
        return merged

    for key, value in overrides.items():
        if isinstance(merged.get(key), dict) and isinstance(value, dict):
            merged[key] = deep_merge_dict(merged[key], value)
        else:
            merged[key] = value
    return merged


def normalize_app_config(config):
    """补齐并校验配置文件"""
    merged = deep_merge_dict(DEFAULT_APP_CONFIG, config if isinstance(config, dict) else {})

    triplet_fields = merged.get('triplet_fields', {})
    for field, defaults in DEFAULT_APP_CONFIG['triplet_fields'].items():
        field_config = triplet_fields.get(field, {})
        normalized_field_config = {}
        for key, default_value in defaults.items():
            value = field_config.get(key, default_value)
            if not isinstance(value, str) or not value.strip():
                value = default_value
            normalized_field_config[key] = value.strip()
        triplet_fields[field] = normalized_field_config

    customization = merged.get('triplet_customization', {})
    customization['allow_custom_input'] = bool(customization.get('allow_custom_input', True))

    custom_option_label = customization.get('custom_option_label', DEFAULT_APP_CONFIG['triplet_customization']['custom_option_label'])
    if not isinstance(custom_option_label, str) or not custom_option_label.strip():
        custom_option_label = DEFAULT_APP_CONFIG['triplet_customization']['custom_option_label']
    customization['custom_option_label'] = custom_option_label.strip()

    merged['triplet_fields'] = triplet_fields
    merged['triplet_customization'] = customization
    return merged


def ensure_default_app_config():
    """在数据目录下创建可编辑的默认配置文件"""
    config_path = get_app_config_path()
    if os.path.exists(config_path):
        return

    try:
        with open(config_path, 'w', encoding='utf-8') as f:
            json.dump(DEFAULT_APP_CONFIG, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"Error creating default app config: {e}")


def load_app_config():
    """加载应用配置文件"""
    config_path = get_app_config_path()
    if not os.path.exists(config_path):
        ensure_default_app_config()

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            loaded = json.load(f)
        return normalize_app_config(loaded)
    except Exception as e:
        print(f"Error loading app config: {e}")
        return copy.deepcopy(DEFAULT_APP_CONFIG)


ensure_default_app_config()


def setup_logging():
    log_path = os.path.join(DATA_FOLDER, 'server.log')
    handler = RotatingFileHandler(log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding='utf-8')
    formatter = logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s')
    handler.setFormatter(formatter)
    handler.setLevel(logging.INFO)

    if not any(getattr(h, 'baseFilename', None) == handler.baseFilename for h in app.logger.handlers):
        app.logger.addHandler(handler)
    app.logger.setLevel(logging.INFO)

    werkzeug_logger = logging.getLogger('werkzeug')
    if not any(getattr(h, 'baseFilename', None) == handler.baseFilename for h in werkzeug_logger.handlers):
        werkzeug_logger.addHandler(handler)
    werkzeug_logger.setLevel(logging.INFO)


setup_logging()

# 支持的视频格式
ALLOWED_VIDEO_EXTENSIONS = {'.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv'}

def get_video_info(video_path):
    """获取视频信息"""
    try:
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return None
        
        fps = cap.get(cv2.CAP_PROP_FPS)
        frame_count = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        duration = frame_count / fps if fps > 0 else 0
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        
        cap.release()
        
        return {
            'fps': fps,
            'frame_count': frame_count,
            'duration': duration,
            'width': width,
            'height': height
        }
    except Exception as e:
        print(f"Error getting video info: {e}")
        return None

def get_annotation_file_path(video_name):
    """获取标注文件路径"""
    video_name_without_ext = os.path.splitext(video_name)[0]
    return resolve_path_under_root(ANNOTATIONS_FOLDER, f"{video_name_without_ext}.json")

def load_annotations(video_name):
    """加载视频的标注数据"""
    annotation_file = get_annotation_file_path(video_name)
    if os.path.exists(annotation_file):
        try:
            with open(annotation_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                # 确保包含completed_manually字段
                if "completed_manually" not in data:
                    data["completed_manually"] = False
                return data
        except Exception as e:
            print(f"Error loading annotations: {e}")
            return {"annotations": [], "video_info": {}, "completed_manually": False}
    return {"annotations": [], "video_info": {}, "completed_manually": False}

def save_annotations(video_name, annotations_data):
    """保存标注数据"""
    annotation_file = get_annotation_file_path(video_name)
    try:
        os.makedirs(os.path.dirname(annotation_file), exist_ok=True)
        with open(annotation_file, 'w', encoding='utf-8') as f:
            json.dump(annotations_data, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"Error saving annotations: {e}")
        return False

def load_triplet_options():
    """从CSV文件加载三元组选项"""
    csv_candidates = [os.path.join(DATA_FOLDER, '三元组.csv')]
    if getattr(sys, 'frozen', False):
        meipass = getattr(sys, '_MEIPASS', None)
        if meipass:
            csv_candidates.append(os.path.join(meipass, 'data', '三元组.csv'))
        # 兼容早期打包产物；当前 onedir 版本统一使用可执行文件同级 data 目录。
        csv_candidates.append(os.path.join(os.path.dirname(sys.executable), '_internal', 'data', '三元组.csv'))
        csv_candidates.append(os.path.join(os.path.dirname(sys.executable), '_internal', '三元组.csv'))

    csv_file = next((path for path in csv_candidates if os.path.exists(path)), None)
    if not csv_file:
        return copy.deepcopy(DEFAULT_TRIPLET_OPTIONS)
    
    instruments = set()
    actions = set()
    targets = set()
    
    try:
        with open(csv_file, 'r', encoding='utf-8') as f:
            reader = csv.reader(f)
            
            # 跳过标题行
            next(reader, None)
            
            for row in reader:
                if len(row) >= 3:
                    # 添加非空的选项
                    if row[0].strip():  # 器械
                        instruments.add(row[0].strip())
                    if row[1].strip():  # 动作
                        actions.add(row[1].strip())
                    if row[2].strip():  # 目标
                        targets.add(row[2].strip())
        
        return {
            'instruments': sorted(list(instruments)),
            'actions': sorted(list(actions)),
            'targets': sorted(list(targets))
        }
    except Exception as e:
        print(f"Error loading triplet options: {e}")
        return copy.deepcopy(DEFAULT_TRIPLET_OPTIONS)

@app.route('/')
def index():
    """主页"""
    try:
        return render_template('index.html', videos_folder=VIDEOS_FOLDER)
    except Exception as e:
        app.logger.exception("Failed to render index.html")
        return f"Template error: {e}", 500

@app.route('/api/videos', methods=['GET'])
def get_videos():
    """获取视频列表"""
    try:
        videos = []
        if os.path.exists(VIDEOS_FOLDER):
            for root, _, filenames in os.walk(VIDEOS_FOLDER):
                filenames.sort()
                for filename in filenames:
                    file_path = os.path.join(root, filename)
                    _, ext = os.path.splitext(filename)
                    if ext.lower() not in ALLOWED_VIDEO_EXTENSIONS:
                        continue

                    relative_path = os.path.relpath(file_path, VIDEOS_FOLDER).replace(os.sep, '/')
                    folder = os.path.dirname(relative_path).replace(os.sep, '/')

                    # 获取视频信息
                    video_info = get_video_info(file_path)

                    # 获取标注进度
                    annotations_data = load_annotations(relative_path)
                    annotation_count = len(annotations_data.get('annotations', []))

                    # 获取文件大小
                    file_size = os.path.getsize(file_path)

                    videos.append({
                        'name': relative_path,
                        'path': relative_path,
                        'display_name': filename,
                        'folder': '' if folder == '.' else folder,
                        'size': file_size,
                        'annotation_count': annotation_count,
                        'video_info': video_info,
                        'completed_manually': annotations_data.get('completed_manually', False)
                    })
            videos.sort(key=lambda item: item['name'].lower())
        
        return jsonify({
            'success': True,
            'videos': videos
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/videos/<path:video_name>/info', methods=['GET'])
def get_video_info_api(video_name):
    """获取特定视频的详细信息"""
    try:
        video_path = get_video_file_path(video_name)
        if not os.path.exists(video_path):
            return jsonify({
                'success': False,
                'error': 'Video not found'
            }), 404
        
        video_info = get_video_info(video_path)
        annotations_data = load_annotations(video_name)
        
        return jsonify({
            'success': True,
            'video_info': video_info,
            'annotations': annotations_data.get('annotations', []),
            'annotation_count': len(annotations_data.get('annotations', []))
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/videos/<path:video_name>/annotations', methods=['GET'])
def get_annotations(video_name):
    """获取视频的标注数据"""
    try:
        annotations_data = load_annotations(video_name)
        return jsonify({
            'success': True,
            'annotations': annotations_data.get('annotations', [])
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/videos/<path:video_name>/annotations', methods=['POST'])
def add_annotation(video_name):
    """添加新的标注"""
    try:
        data = request.get_json()
        
        # 验证必需字段
        required_fields = ['start_frame', 'end_frame', 'instrument', 'action', 'target']
        for field in required_fields:
            if field not in data:
                return jsonify({
                    'success': False,
                    'error': f'Missing required field: {field}'
                }), 400
        
        # 加载现有标注
        annotations_data = load_annotations(video_name)
        
        # 创建新标注
        new_annotation = {
            'id': str(uuid.uuid4()),
            'start_frame': int(data['start_frame']),
            'end_frame': int(data['end_frame']),
            'instrument': data['instrument'].strip(),
            'action': data['action'].strip(),
            'target': data['target'].strip(),
            'created_at': datetime.now().isoformat(),
            'updated_at': datetime.now().isoformat()
        }
        
        # 验证帧范围
        if new_annotation['start_frame'] >= new_annotation['end_frame']:
            return jsonify({
                'success': False,
                'error': 'Start frame must be less than end frame'
            }), 400
        
        # 添加到标注列表
        annotations_data.setdefault('annotations', []).append(new_annotation)
        
        # 保存
        if save_annotations(video_name, annotations_data):
            return jsonify({
                'success': True,
                'annotation': new_annotation
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to save annotation'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/videos/<path:video_name>/annotations/<annotation_id>', methods=['PUT'])
def update_annotation(video_name, annotation_id):
    """更新标注"""
    try:
        data = request.get_json()
        annotations_data = load_annotations(video_name)
        annotations = annotations_data.get('annotations', [])
        
        # 查找要更新的标注
        annotation_index = None
        for i, annotation in enumerate(annotations):
            if annotation['id'] == annotation_id:
                annotation_index = i
                break
        
        if annotation_index is None:
            return jsonify({
                'success': False,
                'error': 'Annotation not found'
            }), 404
        
        # 更新标注
        annotation = annotations[annotation_index]
        if 'start_frame' in data:
            annotation['start_frame'] = int(data['start_frame'])
        if 'end_frame' in data:
            annotation['end_frame'] = int(data['end_frame'])
        if 'instrument' in data:
            annotation['instrument'] = data['instrument'].strip()
        if 'action' in data:
            annotation['action'] = data['action'].strip()
        if 'target' in data:
            annotation['target'] = data['target'].strip()
        
        annotation['updated_at'] = datetime.now().isoformat()
        
        # 验证帧范围
        if annotation['start_frame'] >= annotation['end_frame']:
            return jsonify({
                'success': False,
                'error': 'Start frame must be less than end frame'
            }), 400
        
        # 保存
        if save_annotations(video_name, annotations_data):
            return jsonify({
                'success': True,
                'annotation': annotation
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to save annotation'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/videos/<path:video_name>/annotations/<annotation_id>', methods=['DELETE'])
def delete_annotation(video_name, annotation_id):
    """删除标注"""
    try:
        annotations_data = load_annotations(video_name)
        annotations = annotations_data.get('annotations', [])
        
        # 过滤掉要删除的标注
        original_count = len(annotations)
        annotations_data['annotations'] = [
            annotation for annotation in annotations 
            if annotation['id'] != annotation_id
        ]
        
        if len(annotations_data['annotations']) == original_count:
            return jsonify({
                'success': False,
                'error': 'Annotation not found'
            }), 404
        
        # 保存
        if save_annotations(video_name, annotations_data):
            return jsonify({
                'success': True
            })
        else:
            return jsonify({
                'success': False,
                'error': 'Failed to save changes'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

def convert_avi_to_mp4_stream(video_path):
    """将AVI转换为MP4流"""
    def generate():
        try:
            # 首先尝试使用FFmpeg（如果可用）
            try:
                # 创建临时输出文件
                with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
                    temp_path = temp_file.name
                
                # 使用FFmpeg转换
                cmd = [
                    'ffmpeg', '-i', video_path,
                    '-c:v', 'libx264', '-c:a', 'aac',
                    '-movflags', '+faststart',
                    '-y', temp_path
                ]
                
                result = subprocess.run(cmd, capture_output=True, text=True)
                
                if result.returncode == 0:
                    # 转换成功，流式传输
                    with open(temp_path, 'rb') as f:
                        while True:
                            chunk = f.read(8192)
                            if not chunk:
                                break
                            yield chunk
                    os.unlink(temp_path)
                    return
                
            except (FileNotFoundError, subprocess.SubprocessError):
                print("FFmpeg不可用，使用cv2备用方案")
            
            # FFmpeg不可用时的备用方案：使用cv2
            cap = cv2.VideoCapture(video_path)
            
            # 获取视频属性
            fps = cap.get(cv2.CAP_PROP_FPS)
            width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            
            # 创建临时文件
            with tempfile.NamedTemporaryFile(suffix='.mp4', delete=False) as temp_file:
                temp_path = temp_file.name
            
            # 使用H264编码器
            fourcc = cv2.VideoWriter_fourcc(*'H264')
            out = cv2.VideoWriter(temp_path, fourcc, fps, (width, height))
            
            if not out.isOpened():
                # 如果H264不可用，尝试mp4v
                fourcc = cv2.VideoWriter_fourcc(*'mp4v')
                out = cv2.VideoWriter(temp_path, fourcc, fps, (width, height))
            
            # 逐帧转换
            while True:
                ret, frame = cap.read()
                if not ret:
                    break
                out.write(frame)
            
            cap.release()
            out.release()
            
            # 读取转换后的文件并流式传输
            with open(temp_path, 'rb') as f:
                while True:
                    chunk = f.read(8192)
                    if not chunk:
                        break
                    yield chunk
            
            # 清理临时文件
            os.unlink(temp_path)
            
        except Exception as e:
            print(f"AVI转换错误: {e}")
            # 如果转换失败，直接传输原文件
            try:
                with open(video_path, 'rb') as f:
                    while True:
                        chunk = f.read(8192)
                        if not chunk:
                            break
                        yield chunk
            except Exception as e2:
                print(f"原文件传输也失败: {e2}")
                yield b''
    
    return generate()

@app.route('/videos/<path:filename>')
def serve_video(filename):
    """提供视频文件服务，支持Range请求以便进度条跳转"""
    try:
        video_path = get_video_file_path(filename)
        if not os.path.exists(video_path):
            return jsonify({
                'success': False,
                'error': 'Video file not found'
            }), 404
        
        # 检查文件扩展名
        _, ext = os.path.splitext(filename)
        if ext.lower() not in ALLOWED_VIDEO_EXTENSIONS:
            return jsonify({
                'success': False,
                'error': 'Unsupported video format'
            }), 400
        
        # 对AVI格式进行实时转换
        if ext.lower() == '.avi':
            return Response(
                convert_avi_to_mp4_stream(video_path),
                mimetype='video/mp4',
                headers={
                    'Content-Type': 'video/mp4',
                    'Accept-Ranges': 'bytes'
                }
            )
        
        # 其他格式正常处理
        return send_from_directory(
            os.path.dirname(video_path),
            os.path.basename(filename),
            conditional=True  # 支持HTTP Range请求
        )
            
    except Exception as e:
        print(f"Error serving video {filename}: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/triplet-options', methods=['GET'])
def get_triplet_options():
    """获取三元组选项"""
    try:
        options = load_triplet_options()
        app_config = load_app_config()
        return jsonify({
            'success': True,
            'options': options,
            'ui_config': app_config
        })
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/videos/<path:video_name>/complete', methods=['PUT'])
def set_video_completion_status(video_name):
    """设置视频的手动完成状态"""
    try:
        data = request.get_json()
        completed_manually = data.get('completed_manually', False)
        
        # 加载现有的标注数据
        annotations_data = load_annotations(video_name)
        
        # 更新手动完成状态
        annotations_data['completed_manually'] = completed_manually
        
        # 保存更新后的数据
        if save_annotations(video_name, annotations_data):
            return jsonify({
                'success': True,
                'message': '视频完成状态已更新',
                'completed_manually': completed_manually
            })
        else:
            return jsonify({
                'success': False,
                'error': '保存失败'
            }), 500
            
    except Exception as e:
        return jsonify({
            'success': False,
            'error': str(e)
        }), 500

@app.route('/api/health', methods=['GET'])
def health_check():
    """健康检查"""
    return jsonify({
        'success': True,
        'message': 'Video annotation system is running',
        'timestamp': datetime.now().isoformat()
    })

if __name__ == '__main__':
    import webbrowser
    import threading
    
    print(f"启动{APP_NAME}...")
    print(f"视频文件夹: {os.path.abspath(VIDEOS_FOLDER)}")
    print(f"标注文件夹: {os.path.abspath(ANNOTATIONS_FOLDER)}")
    print(f"请将待标注的视频文件放入: {os.path.abspath(VIDEOS_FOLDER)}")
    print("系统将自动打开浏览器...")
    
    def open_browser():
        """延迟打开浏览器"""
        webbrowser.open('http://localhost:4000')
    
    # 延迟1.5秒后自动打开浏览器，确保Flask服务完全启动
    timer = threading.Timer(1.5, open_browser)
    timer.start()
    
    # 生产模式运行，关闭调试模式以避免自动重载
    app.run(debug=False, host='0.0.0.0', port=4000)
