"""
VideoMaker - Unified Flask app combining LongForm Maker and ShortForm Maker.
Runs on port 5001.
"""

import os
import sys
import json
import uuid
import subprocess
import tempfile
import time
import base64
import shutil
from pathlib import Path

from flask import Flask, request, jsonify, send_file, render_template
from dotenv import load_dotenv
load_dotenv()

# ── App setup ──
APP_ROOT = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024 * 1024  # 2GB
app.config['OUTPUT_FOLDER'] = os.path.join(APP_ROOT, 'output')
app.config['TEMP_FOLDER'] = os.path.join(APP_ROOT, 'temp')

# Ensure directories exist
os.makedirs(app.config['OUTPUT_FOLDER'], exist_ok=True)
os.makedirs(app.config['TEMP_FOLDER'], exist_ok=True)
os.makedirs(os.path.join(APP_ROOT, 'outputs'), exist_ok=True)

# ── Longform module imports ──
from modules.youtube_search import search_videos
from modules.subtitle_collector import collect_subtitles
from modules.bench_analyzer import analyze_bench
from modules.script_generator import generate_script, save_and_reparse
from modules.image_generator import generate_images
from modules.tts_generator import generate_tts, get_voices
from modules.silence_remover import remove_silence
from modules.video_assembler import assemble_video
from modules.thumbnail_generator import generate_thumbnail

# ── Shortform module import ──
from modules import shortform as sf
sf.init(APP_ROOT)

import yt_dlp


# ============================================================
#  LANDING PAGE
# ============================================================

@app.route('/')
def index():
    return render_template('index.html')


# ============================================================
#  LONGFORM ROUTES
# ============================================================

@app.route('/longform')
def longform():
    return render_template('longform.html')


@app.route('/api/search', methods=['POST'])
def lf_search():
    data = request.json
    keyword = data.get('keyword', '')
    max_results = data.get('max_results', 10)
    order = data.get('order', 'viewCount')
    if not keyword:
        return jsonify({'success': False, 'error': '키워드를 입력하세요'})
    try:
        result = search_videos(keyword, max_results, order)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/collect', methods=['POST'])
def lf_collect():
    data = request.json
    urls = data.get('urls', [])
    session_id = data.get('session_id')
    try:
        result = collect_subtitles(urls, session_id)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/analyze', methods=['POST'])
def lf_analyze():
    data = request.json
    session_id = data.get('session_id')
    try:
        result = analyze_bench(session_id)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/script', methods=['POST'])
def lf_script():
    data = request.json
    session_id = data.get('session_id')
    topic = data.get('topic', '')
    custom_prompt = data.get('custom_prompt', '')
    try:
        result = generate_script(session_id, topic, custom_prompt)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/script/save', methods=['POST'])
def lf_save_script():
    data = request.json
    session_id = data.get('session_id')
    script_text = data.get('script')
    try:
        result = save_and_reparse(session_id, script_text)
        return jsonify({'success': True, 'scene_count': result['scene_count']})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/images', methods=['POST'])
def lf_images():
    data = request.json
    session_id = data.get('session_id')
    style = data.get('style', '사실적인 사진')
    images_per_scene = data.get('images_per_scene', 2)
    try:
        result = generate_images(session_id, style, images_per_scene)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/tts', methods=['POST'])
def lf_tts():
    data = request.json
    session_id = data.get('session_id')
    voice_id = data.get('voice_id', 'Rachel')
    try:
        result = generate_tts(session_id, voice_id)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/silence', methods=['POST'])
def lf_silence():
    data = request.json
    session_id = data.get('session_id')
    try:
        result = remove_silence(session_id)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/assemble', methods=['POST'])
def lf_assemble():
    data = request.json
    session_id = data.get('session_id')
    add_subtitle = data.get('add_subtitle', True)
    subtitle_style = data.get('subtitle_style', 'default')
    try:
        result = assemble_video(session_id, add_subtitle, subtitle_style)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/voices')
def lf_voices():
    try:
        result = get_voices()
        return jsonify({'success': True, 'voices': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/thumbnail', methods=['POST'])
def lf_thumbnail():
    data = request.json
    session_id = data.get('session_id')
    title = data.get('title', '')
    try:
        result = generate_thumbnail(session_id, title)
        return jsonify({'success': True, 'data': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)})


@app.route('/api/download/<session_id>')
def lf_download(session_id):
    path = os.path.join(app.config['OUTPUT_FOLDER'], f'{session_id}_final.mp4')
    if os.path.exists(path):
        return send_file(path, as_attachment=True)
    return jsonify({'error': '파일 없음'}), 404


# ============================================================
#  LONGFORM -> SHORTFORM CONNECTION (direct, no HTTP proxy)
# ============================================================

@app.route('/api/shortform', methods=['POST'])
def lf_create_shortform():
    """Process the completed longform video through the shortform pipeline directly."""
    data = request.json
    session_id = data.get('session_id')
    video_path = os.path.join(app.config['OUTPUT_FOLDER'], f'{session_id}_final.mp4')

    if not os.path.exists(video_path):
        return jsonify({'success': False, 'error': '완성된 영상이 없습니다'}), 404

    try:
        num_shorts = int(data.get('num_shorts', 7))
        duration_preset = data.get('duration_preset', '30-60')
        duration_map = {"15-30": (15,30), "30-60": (30,60), "60-90": (60,90), "90-120": (90,120)}
        min_sec, max_sec = duration_map.get(duration_preset, (30, 60))

        # Create shortform session
        sf_session_id = uuid.uuid4().hex
        sf_session_dir = sf.OUTPUT_DIR / sf_session_id
        sf_session_dir.mkdir(exist_ok=True)

        # Copy the longform video as source
        source_path = str(sf_session_dir / "source.mp4")
        shutil.copy2(video_path, source_path)

        print(f"[{sf_session_id}] Whisper transcription...")
        transcript = sf.transcribe_video(source_path)

        print(f"[{sf_session_id}] Claude segment analysis...")
        shorts = sf.extract_shorts_with_claude(transcript, num_shorts, min_sec, max_sec)

        results = []
        for i, short in enumerate(shorts):
            short_id = f"short_{i+1}"
            output_path = str(sf_session_dir / f"{short_id}.mp4")
            print(f"[{sf_session_id}] Generating {short_id}... ({short['title']})")
            success = sf.cut_video(source_path, short["segments"], output_path)
            if success:
                s0 = short["segments"][0]["start"]
                se = short["segments"][-1]["end"]
                timeline = f"{int(s0//60)}분 {int(s0%60):02d}초 ~ {int(se//60)}분 {int(se%60):02d}초"
                results.append({
                    "id": short_id,
                    "session_id": sf_session_id,
                    "title": short["title"],
                    "description": short["description"],
                    "hashtags": short.get("hashtags", []),
                    "segments": short["segments"],
                    "clip_script": short.get("clip_script", ""),
                    "timeline": timeline,
                    "duration": short.get("duration", 0),
                    "desire_key": short.get("desire_key", ""),
                    "select_reason": short.get("select_reason", ""),
                    "download_url": f"/api/sf/download/{sf_session_id}/{short_id}",
                    "stream_url": f"/api/sf/stream/{sf_session_id}/{short_id}"
                })

        meta_path = sf_session_dir / "meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        sf.save_project({
            "session_id": sf_session_id,
            "title": f"롱폼→숏폼 ({session_id[:8]})",
            "shorts_count": len(results),
            "created_at": time.strftime("%Y. %m. %d."),
        })

        return jsonify({
            'success': True,
            'data': {
                'session_id': sf_session_id,
                'shorts': results,
                'shortform_url': '/shortform'
            }
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
#  SHORTFORM ROUTES (all prefixed with /api/sf/)
# ============================================================

@app.route('/shortform')
def shortform():
    return render_template('shortform.html')


@app.route('/api/sf/youtube-info', methods=['POST'])
def sf_youtube_info():
    url = request.json.get("url", "")
    if not url:
        return jsonify({"error": "URL 없음"}), 400
    try:
        ydl_opts = {"quiet": True, "skip_download": True}
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
        return jsonify({
            "title": info.get("title", ""),
            "thumbnail": info.get("thumbnail", ""),
            "duration": info.get("duration", 0),
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/sf/process', methods=['POST'])
def sf_process_video():
    session_id = uuid.uuid4().hex
    session_dir = sf.OUTPUT_DIR / session_id
    session_dir.mkdir(exist_ok=True)
    try:
        video_title = "영상"
        if "youtube_url" in request.form and request.form["youtube_url"]:
            url = request.form["youtube_url"]
            video_path = str(session_dir / "source.mp4")
            print(f"[{session_id}] YouTube download...")
            video_title = sf.download_youtube(url, video_path)
        elif "video_file" in request.files:
            file = request.files["video_file"]
            video_path = str(session_dir / "source.mp4")
            video_title = file.filename or "업로드 영상"
            file.save(video_path)
        else:
            return jsonify({"error": "영상 URL 또는 파일을 제공해주세요"}), 400

        num_shorts = int(request.form.get("num_shorts", 7))
        duration_preset = request.form.get("duration_preset", "30-60")
        duration_map = {"15-30": (15,30), "30-60": (30,60), "60-90": (60,90), "90-120": (90,120)}
        min_sec, max_sec = duration_map.get(duration_preset, (30, 60))

        print(f"[{session_id}] Whisper transcription...")
        transcript = sf.transcribe_video(video_path)

        print(f"[{session_id}] Claude segment analysis...")
        shorts = sf.extract_shorts_with_claude(transcript, num_shorts, min_sec, max_sec)

        results = []
        for i, short in enumerate(shorts):
            short_id = f"short_{i+1}"
            output_path = str(session_dir / f"{short_id}.mp4")
            print(f"[{session_id}] Generating {short_id}... ({short['title']})")
            success = sf.cut_video(video_path, short["segments"], output_path)
            if success:
                s0 = short["segments"][0]["start"]
                se = short["segments"][-1]["end"]
                timeline = f"{int(s0//60)}분 {int(s0%60):02d}초 ~ {int(se//60)}분 {int(se%60):02d}초"
                results.append({
                    "id": short_id,
                    "session_id": session_id,
                    "title": short["title"],
                    "description": short["description"],
                    "hashtags": short.get("hashtags", []),
                    "segments": short["segments"],
                    "clip_script": short.get("clip_script", ""),
                    "timeline": timeline,
                    "duration": short.get("duration", 0),
                    "desire_key": short.get("desire_key", ""),
                    "select_reason": short.get("select_reason", ""),
                    "download_url": f"/api/sf/download/{session_id}/{short_id}",
                    "stream_url": f"/api/sf/stream/{session_id}/{short_id}"
                })

        meta_path = session_dir / "meta.json"
        with open(meta_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

        sf.save_project({
            "session_id": session_id,
            "title": video_title,
            "shorts_count": len(results),
            "created_at": time.strftime("%Y. %m. %d."),
        })

        return jsonify({"session_id": session_id, "shorts": results})

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/api/sf/projects', methods=['GET'])
def sf_get_projects():
    return jsonify(sf.load_projects())


@app.route('/api/sf/projects/<session_id>', methods=['GET', 'DELETE'])
def sf_get_project(session_id):
    if request.method == "DELETE":
        projects = sf.load_projects()
        projects = [p for p in projects if p["session_id"] != session_id]
        with open(sf.PROJECTS_FILE, "w", encoding="utf-8") as f:
            json.dump(projects, f, ensure_ascii=False, indent=2)
        session_dir = sf.OUTPUT_DIR / session_id
        if session_dir.exists():
            shutil.rmtree(session_dir)
        return jsonify({"ok": True})

    # GET
    meta_path = sf.OUTPUT_DIR / session_id / "meta.json"
    if not meta_path.exists():
        return jsonify({"error": "없음"}), 404
    with open(meta_path, "r", encoding="utf-8") as f:
        return jsonify(json.load(f))


@app.route('/api/sf/download/<session_id>/<short_id>', methods=['GET', 'POST'])
def sf_download_short(session_id, short_id):
    raw_path = sf.OUTPUT_DIR / session_id / f"{short_id}.mp4"
    if not raw_path.exists():
        return jsonify({"error": "파일 없음"}), 404

    if request.method == "POST":
        data  = request.get_json(silent=True) or {}
        title = data.get("title", "")
        png_b64 = data.get("title_png_base64", "")
    else:
        title   = request.args.get("title", "")
        png_b64 = ""

    if not title:
        meta_path = sf.OUTPUT_DIR / session_id / "meta.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                metas = json.load(f)
            for m in metas:
                if m["id"] == short_id:
                    title = m.get("title", "")
                    break

    if not title:
        return send_file(str(raw_path), as_attachment=True, download_name=f"{short_id}.mp4")

    safe_title = "".join(c for c in title.replace('\n',' ') if c.isalnum() or c in " _-가-힣")[:30]
    fname = f"{safe_title}.mp4" if safe_title else f"{short_id}.mp4"

    out_tmp = tempfile.NamedTemporaryFile(suffix=".mp4", delete=False)
    out_tmp.close()
    title_img_path = None

    try:
        if png_b64:
            png_data = base64.b64decode(png_b64)
            title_tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
            title_tmp.write(png_data)
            title_tmp.close()
            title_img_path = title_tmp.name
            title_y = 0
        else:
            font_name = data.get("font", "GmarketSans Bold") if request.method == "POST" else "GmarketSans Bold"
            font_size = int(data.get("size", 76)) if request.method == "POST" else 76
            color     = data.get("color", "ffffff") if request.method == "POST" else "ffffff"
            outline   = data.get("outline", "strong") if request.method == "POST" else "strong"
            line_h    = int(data.get("line_h", 16)) if request.method == "POST" else 16
            title_img_path, title_img_h = sf.make_title_image(
                title, font_name=font_name, font_size=font_size,
                color=color, outline=outline, line_h=line_h)
            title_y = max(20, 420 - title_img_h - 5)

        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", str(raw_path),
            "-i", title_img_path,
            "-filter_complex", f"[0:v][1:v]overlay=0:{title_y}[out]",
            "-map", "[out]", "-map", "0:a?",
            "-c:v", "libx264", "-crf", "0", "-c:a", "aac",
            "-preset", "ultrafast", "-pix_fmt", "yuv420p",
            "-colorspace", "bt709", "-color_primaries", "bt709",
            "-color_trc", "bt709", "-color_range", "1",
            out_tmp.name
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if result.returncode != 0:
            print("download stderr:", result.stderr.decode(errors="ignore")[:300])
            return send_file(str(raw_path), as_attachment=True, download_name=fname)

        return send_file(out_tmp.name, as_attachment=True, download_name=fname)

    except Exception as e:
        print("download error:", e)
        return send_file(str(raw_path), as_attachment=True, download_name=fname)
    finally:
        if title_img_path and os.path.exists(title_img_path):
            try: os.unlink(title_img_path)
            except: pass


@app.route('/api/sf/stream/<session_id>/<short_id>')
def sf_stream_short(session_id, short_id):
    file_path = sf.OUTPUT_DIR / session_id / f"{short_id}.mp4"
    if not file_path.exists():
        return jsonify({"error": "파일 없음"}), 404
    return send_file(str(file_path), mimetype="video/mp4")


@app.route('/api/sf/update-meta', methods=['POST'])
def sf_update_meta():
    data = request.json
    session_id = data.get("session_id")
    short_id = data.get("short_id")
    meta_path = sf.OUTPUT_DIR / session_id / "meta.json"
    if not meta_path.exists():
        return jsonify({"error": "세션 없음"}), 404
    with open(meta_path, "r", encoding="utf-8") as f:
        metas = json.load(f)
    for item in metas:
        if item["id"] == short_id:
            for field in ["title", "description", "hashtags"]:
                if field in data:
                    item[field] = data[field]
            break
    with open(meta_path, "w", encoding="utf-8") as f:
        json.dump(metas, f, ensure_ascii=False, indent=2)
    return jsonify({"success": True})


@app.route('/api/sf/suggest-title', methods=['POST'])
def sf_suggest_title():
    data = request.json
    content = data.get("clip_script") or data.get("description", "")
    try:
        titles = sf.suggest_titles(content)
        return jsonify({"titles": titles})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route('/api/sf/source-stream/<session_id>')
def sf_source_stream(session_id):
    """Stream the original source video for segment editing preview."""
    source_path = sf.OUTPUT_DIR / session_id / "source.mp4"
    if not source_path.exists():
        return jsonify({"error": "소스 영상 없음"}), 404
    return send_file(str(source_path), mimetype="video/mp4")


@app.route('/api/sf/recut', methods=['POST'])
def sf_recut():
    """Re-cut a clip with new start/end segments."""
    data = request.json
    session_id = data.get("session_id")
    short_id = data.get("short_id")
    new_segments = data.get("segments")  # [{start, end}, ...]

    if not session_id or not short_id or not new_segments:
        return jsonify({"error": "필수 파라미터 누락"}), 400

    source_path = sf.OUTPUT_DIR / session_id / "source.mp4"
    if not source_path.exists():
        return jsonify({"error": "소스 영상 없음"}), 404

    output_path = str(sf.OUTPUT_DIR / session_id / f"{short_id}.mp4")

    try:
        success = sf.cut_video(str(source_path), new_segments, output_path)
        if not success:
            return jsonify({"error": "영상 재생성 실패"}), 500

        # Update meta.json with new segments
        meta_path = sf.OUTPUT_DIR / session_id / "meta.json"
        if meta_path.exists():
            with open(meta_path, "r", encoding="utf-8") as f:
                metas = json.load(f)
            for item in metas:
                if item["id"] == short_id:
                    item["segments"] = new_segments
                    duration = sum(s["end"] - s["start"] for s in new_segments)
                    item["duration"] = round(duration, 1)
                    s0 = new_segments[0]["start"]
                    se = new_segments[-1]["end"]
                    item["timeline"] = f"{int(s0//60)}분 {int(s0%60):02d}초 ~ {int(se//60)}분 {int(se%60):02d}초"
                    break
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(metas, f, ensure_ascii=False, indent=2)

        duration = sum(s["end"] - s["start"] for s in new_segments)
        s0 = new_segments[0]["start"]
        se = new_segments[-1]["end"]
        timeline = f"{int(s0//60)}분 {int(s0%60):02d}초 ~ {int(se//60)}분 {int(se%60):02d}초"

        return jsonify({
            "success": True,
            "duration": round(duration, 1),
            "timeline": timeline
        })

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({"error": str(e)}), 500


@app.route('/static/fonts/<path:filename>')
def serve_font(filename):
    fonts_dir = os.path.join(APP_ROOT, "fonts")
    return send_file(os.path.join(fonts_dir, filename))


# ============================================================
#  RUN
# ============================================================

if __name__ == '__main__':
    app.run(debug=True, port=5001)
