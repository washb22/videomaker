"""
Shortform video maker module - pure functions only (no Flask routes).
Handles: download, transcription, AI analysis, video cutting, title rendering.
"""

import os
import json
import subprocess
import tempfile
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

import anthropic
import openai
import yt_dlp
from PIL import Image, ImageDraw, ImageFont

# ── API clients (initialized at import time) ──
claude = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
oai = openai.OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

# ── Paths (relative to app root, set by init()) or defaults ──
APP_ROOT = Path(os.environ.get("VIDEOMAKER_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
OUTPUT_DIR = APP_ROOT / "outputs"
PROJECTS_FILE = APP_ROOT / "projects.json"
FONTS_DIR = APP_ROOT / "fonts"

OUTPUT_DIR.mkdir(exist_ok=True)


def init(app_root: str):
    """Call once from app.py to set paths relative to the app root."""
    global APP_ROOT, OUTPUT_DIR, PROJECTS_FILE, FONTS_DIR
    APP_ROOT = Path(app_root)
    OUTPUT_DIR = APP_ROOT / "outputs"
    PROJECTS_FILE = APP_ROOT / "projects.json"
    FONTS_DIR = APP_ROOT / "fonts"
    OUTPUT_DIR.mkdir(exist_ok=True)


# ── Project persistence ──

def load_projects():
    if PROJECTS_FILE.exists():
        with open(PROJECTS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def save_project(project: dict):
    projects = load_projects()
    projects = [p for p in projects if p["session_id"] != project["session_id"]]
    projects.insert(0, project)
    projects = projects[:20]
    with open(PROJECTS_FILE, "w", encoding="utf-8") as f:
        json.dump(projects, f, ensure_ascii=False, indent=2)


# ── YouTube download ──

def download_youtube(url: str, output_path: str) -> str:
    ydl_opts = {
        'format': 'bestvideo[height<=1080]+bestaudio/best[height<=1080]/best',
        'outtmpl': output_path.replace('.mp4', '.%(ext)s'),
        'merge_output_format': 'mp4',
        'quiet': True,
        'postprocessors': [{
            'key': 'FFmpegVideoConvertor',
            'preferedformat': 'mp4',
        }],
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        return info.get("title", "영상")


# ── Transcription ──

def transcribe_video(video_path: str) -> dict:
    """OpenAI Whisper API로 음성인식"""
    audio_tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
    audio_tmp.close()
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", video_path,
            "-vn", "-ar", "16000", "-ac", "1",
            "-b:a", "32k",
            audio_tmp.name
        ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        size_mb = os.path.getsize(audio_tmp.name) / (1024 * 1024)
        if size_mb > 24:
            return transcribe_large_audio(audio_tmp.name)

        with open(audio_tmp.name, "rb") as f:
            response = oai.audio.transcriptions.create(
                model="whisper-1",
                file=f,
                language="ko",
                response_format="verbose_json",
                timestamp_granularities=["segment"]
            )

        segments = []
        for seg in response.segments:
            segments.append({
                "start": round(seg.start, 2),
                "end":   round(seg.end,   2),
                "text":  seg.text.strip()
            })
        return {"full_text": response.text, "segments": segments}

    finally:
        if os.path.exists(audio_tmp.name):
            os.unlink(audio_tmp.name)


def transcribe_large_audio(audio_path: str) -> dict:
    """25MB 초과 오디오 -> 10분 단위로 분할해서 각각 API 호출"""
    probe = subprocess.run([
        "ffprobe", "-v", "quiet", "-print_format", "json",
        "-show_format", audio_path
    ], capture_output=True, text=True)
    duration = float(json.loads(probe.stdout)["format"]["duration"])

    chunk_sec = 600
    all_segments = []
    full_text_parts = []

    offset = 0
    while offset < duration:
        chunk_tmp = tempfile.NamedTemporaryFile(suffix=".mp3", delete=False)
        chunk_tmp.close()
        try:
            subprocess.run([
                "ffmpeg", "-y", "-i", audio_path,
                "-ss", str(offset), "-t", str(chunk_sec),
                "-vn", chunk_tmp.name
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            with open(chunk_tmp.name, "rb") as f:
                resp = oai.audio.transcriptions.create(
                    model="whisper-1", file=f, language="ko",
                    response_format="verbose_json",
                    timestamp_granularities=["segment"]
                )
            for seg in resp.segments:
                all_segments.append({
                    "start": round(seg.start + offset, 2),
                    "end":   round(seg.end   + offset, 2),
                    "text":  seg.text.strip()
                })
            full_text_parts.append(resp.text)
        finally:
            if os.path.exists(chunk_tmp.name):
                os.unlink(chunk_tmp.name)
        offset += chunk_sec

    return {"full_text": " ".join(full_text_parts), "segments": all_segments}


# ── AI analysis ──

def get_clip_script(transcript: dict, segments: list) -> str:
    texts = []
    for seg in segments:
        start = seg["start"]
        end = seg["end"]
        for s in transcript["segments"]:
            if s["end"] > start and s["start"] < end:
                texts.append(s["text"])
    seen = set()
    unique = []
    for t in texts:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return " ".join(unique)


def analyze_video_content(transcript: dict) -> dict:
    """1단계: 전체 영상 핵심 분석"""
    full_text = transcript["full_text"]

    if len(full_text) > 4000:
        chunk = len(full_text) // 3
        sampled = full_text[:1500] + "\n...(중략)...\n" + full_text[chunk:chunk+1000] + "\n...(중략)...\n" + full_text[-1500:]
    else:
        sampled = full_text

    prompt = f"""다음은 영상 전체 스크립트야. 이 영상을 깊게 분석해줘.

【전체 스크립트】
{sampled}

아래 항목을 분석해서 JSON으로만 답해:

{{
  "main_topic": "영상의 핵심 주제 한 줄",
  "content_type": "강의/브이로그/인터뷰/리뷰/토크 중 하나",
  "target_audience": "이 영상의 주 타겟 (예: 스마트스토어 초보 셀러)",
  "key_insights": [
    "핵심 인사이트 또는 팁 1 (구체적으로)",
    "핵심 인사이트 또는 팁 2",
    "핵심 인사이트 또는 팁 3"
  ],
  "emotional_peaks": [
    "감정이 고조되거나 임팩트 강한 발언이나 장면 묘사 1",
    "감정이 고조되거나 임팩트 강한 발언이나 장면 묘사 2"
  ],
  "hook_moments": [
    "숏폼으로 잘랐을 때 사람들이 끝까지 볼 것 같은 순간 묘사 1",
    "숏폼으로 잘랐을 때 사람들이 끝까지 볼 것 같은 순간 묘사 2"
  ],
  "desire_flow": "이 영상을 보는 사람들의 핵심 욕구/고민 (예: 내 상품이 왜 안 팔리는지 알고싶다)"
}}"""

    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=1000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    try:
        return json.loads(raw)
    except Exception:
        return {"main_topic": "", "key_insights": [], "desire_flow": "", "hook_moments": []}


def extract_segments_with_claude(transcript: dict, num_shorts: int, min_sec: int, max_sec: int) -> list:
    """2단계: 영상 분석 결과 기반으로 최적 구간 선택"""
    analysis = analyze_video_content(transcript)
    segments_json = json.dumps(transcript["segments"], ensure_ascii=False)

    prompt = f"""너는 숏폼 콘텐츠 기획 전문가야. 영상 분석 결과를 보고 최적의 숏폼 구간을 선택해야 해.

【영상 분석 결과】
- 주제: {analysis.get('main_topic', '')}
- 타겟: {analysis.get('target_audience', '')}
- 핵심 인사이트: {json.dumps(analysis.get('key_insights', []), ensure_ascii=False)}
- 감정 클라이맥스: {json.dumps(analysis.get('emotional_peaks', []), ensure_ascii=False)}
- 후킹 포인트: {json.dumps(analysis.get('hook_moments', []), ensure_ascii=False)}
- 시청자 욕구: {analysis.get('desire_flow', '')}

【타임스탬프별 세그먼트】
{segments_json}

위 분석을 바탕으로 숏폼 {num_shorts}개를 선택해줘.

구간 선택 기준 (중요도 순):
1. 핵심 인사이트/팁이 명확하게 전달되는 구간 (가장 중요)
2. 감정 고조 / 공감 폭발 / 반전이 있는 구간
3. 후킹 포인트 → 핵심 → 결론 구조가 한 클립 안에 완결되는 구간
4. 영상 맥락 없이도 혼자 이해되는 구간
5. 절대 피해야 할 구간: 인사말, 채널 소개, 광고, 잡담

조건:
- 각 구간: {min_sec}초 ~ {max_sec}초
- 서로 겹치지 않게
- 영상 전체에 고르게 분포 (앞부분만 선택 금지)

반드시 아래 JSON 배열 형식으로만 답해:
[
  {{
    "segments": [{{"start": 시작초, "end": 끝초}}],
    "reason": "이 구간을 선택한 이유 한 줄"
  }}
]"""

    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]

    result = json.loads(raw)
    extract_segments_with_claude._last_analysis = analysis
    return result


def generate_title_for_clip(full_text: str, clip_script: str, analysis: dict = None) -> dict:
    """3단계: 클립의 욕맥 파악 후 후킹 제목 생성"""
    desire_flow  = analysis.get('desire_flow', '')  if analysis else ''
    target       = analysis.get('target_audience', '') if analysis else ''
    main_topic   = analysis.get('main_topic', '') if analysis else ''

    prompt = f"""너는 숏폼 제목으로 실제 10만+ 조회수를 뽑아낸 콘텐츠 전문가야.

【영상 전체 맥락】
- 주제: {main_topic}
- 타겟: {target}
- 시청자 핵심 욕구/고민: {desire_flow}

【이 클립의 실제 대화】
{clip_script}

━━━ STEP 1: 욕맥 파악 (내부 분석용) ━━━
이 클립을 보는 사람의 핵심 고민/욕구가 뭔가?
이 클립이 그 욕구를 어떻게 건드리는가?

━━━ STEP 2: 후킹 패턴 선택 ━━━
실제 10만+ 조회수 패턴:
1) 긴급성  → "이거 모르면 평생 후회함", "지금 안 하면 진짜 늦음"
2) 숫자결과 → "월 300 버는 사람들 공통점", "38일 만에 매출 3배"
3) 공감자극 → "열심히 하는데 왜 안 팔릴까 ㄹㅇ", "이걸 몰라서 돈 날렸음"
4) 타겟호출 → "스마트스토어 하는 사람 집중", "아직도 이렇게 하고 있어? ㄷㄷ"
5) 반전호기심 → "잘 나가는 셀러들이 숨기는 것", "다들 반대로 하고 있음 ㄹㅇ"

━━━ STEP 3: 제목 작성 규칙 ━━━
- 클립 실제 내용 기반 (동떨어진 제목 절대 금지)
- 전체 20자 이내
- 마침표 금지, 구어체 적극 활용
- "방법" "하는 법" 절대 금지
- 줄바꿈: 의미 단위로 \\n 삽입 (한 줄 9~11자 기준)

반드시 아래 JSON 형식으로만 답해:
{{
  "desire_key": "이 클립이 건드리는 핵심 욕구 한 줄",
  "title": "후킹 제목\\n줄바꿈 포함",
  "description": "클립 핵심 내용 2줄 요약",
  "hashtags": ["#태그1", "#태그2", "#태그3"]
}}"""

    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}]
    )
    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"): raw = raw[4:]
    return json.loads(raw)


def extract_shorts_with_claude(transcript: dict, num_shorts: int, min_sec: int, max_sec: int) -> list:
    raw_segments = extract_segments_with_claude(transcript, num_shorts, min_sec, max_sec)
    analysis = getattr(extract_segments_with_claude, '_last_analysis', {})
    shorts = []
    for item in raw_segments:
        segs = item["segments"]
        clip_script = get_clip_script(transcript, segs)
        if not clip_script.strip():
            continue
        meta = generate_title_for_clip(transcript["full_text"], clip_script, analysis)
        duration = sum(s["end"] - s["start"] for s in segs)
        shorts.append({
            "title": meta["title"],
            "description": meta["description"],
            "hashtags": meta.get("hashtags", []),
            "segments": segs,
            "clip_script": clip_script,
            "duration": round(duration, 1),
            "desire_key": meta.get("desire_key", ""),
            "select_reason": item.get("reason", "")
        })
    return shorts


# ── Font handling ──

def find_korean_font(font_name: str = "") -> str:
    fonts_dir = str(FONTS_DIR)

    font_file_map = {
        "GmarketSans Bold":         "GmarketSansTTFBold.ttf",
        "GmarketSans Light":        "GmarketSansTTFLight.ttf",
        "GmarketSans Medium":       "GmarketSansTTFMedium.ttf",
        "NotoSansKR ExtraBold":     "NotoSansKR-ExtraBold.ttf",
        "NotoSansKR Black":         "NotoSansKR-Black.ttf",
        "tvN 즐거운이야기 Bold":    "tvN 즐거운이야기 Bold.ttf",
        "tvN 즐거운이야기 Light":   "tvN 즐거운이야기 Light.ttf",
        "KoPub Dotum Bold":         "KoPub Dotum Bold.ttf",
    }

    if font_name in font_file_map:
        path = os.path.join(fonts_dir, font_file_map[font_name])
        if os.path.exists(path):
            return path

    if os.path.isdir(fonts_dir):
        n = font_name.lower().replace(" ", "").replace("-", "")
        for f in sorted(os.listdir(fonts_dir)):
            fl = f.lower().replace(" ", "").replace("-", "")
            if n in fl and (f.endswith(".ttf") or f.endswith(".otf")):
                return os.path.join(fonts_dir, f)
        for f in sorted(os.listdir(fonts_dir)):
            if "bold" in f.lower() and (f.endswith(".ttf") or f.endswith(".otf")):
                return os.path.join(fonts_dir, f)

    for p in [
        "C:/Windows/Fonts/malgunbd.ttf",
        "C:/Windows/Fonts/malgun.ttf",
        "C:/Windows/Fonts/NanumGothicBold.ttf",
    ]:
        if os.path.exists(p):
            return p
    return ""


def hex_to_rgb(h: str) -> tuple:
    h = h.lstrip('#')
    try:
        return (int(h[0:2],16), int(h[2:4],16), int(h[4:6],16), 255)
    except Exception:
        return (255, 255, 255, 255)


def make_title_image(title: str, canvas_w: int = 1080,
                     font_size: int = 76, font_name: str = "Nanum Square Round",
                     color: str = "ffffff", outline: str = "strong",
                     line_h: int = 16, segments: list = None) -> tuple:
    """segments: [{start, end, color?, font?}] -- 글자 인덱스 기준 구간별 스타일"""
    if segments is None:
        segments = []

    font_path = find_korean_font(font_name)
    try:
        base_font = ImageFont.truetype(font_path, font_size) if font_path else ImageFont.load_default()
    except Exception:
        base_font = ImageFont.load_default()

    tmp_img  = Image.new("RGBA", (1, 1))
    tmp_draw = ImageDraw.Draw(tmp_img)
    max_w = 920

    if '\n' in title:
        raw_lines = title.split('\n')
    else:
        words = title.split(' ')
        raw_lines, current = [], ''
        for word in words:
            test = (current + ' ' + word).strip()
            bbox = tmp_draw.textbbox((0,0), test, font=base_font)
            if bbox[2]-bbox[0] > max_w and current:
                raw_lines.append(current); current = word
            else:
                current = test
        if current: raw_lines.append(current)

    lines = []
    char_offset = 0
    for line in raw_lines:
        bbox = tmp_draw.textbbox((0,0), line, font=base_font)
        if bbox[2]-bbox[0] > max_w:
            t, off = line, char_offset
            while len(t) > 11:
                lines.append({'text': t[:11], 'offset': off})
                off += 11; t = t[11:]
            if t: lines.append({'text': t, 'offset': off})
        else:
            lines.append({'text': line, 'offset': char_offset})
        char_offset += len(line) + 1

    lh = font_size + line_h
    padding_v = 20
    img_h = lh * len(lines) + padding_v * 2
    img  = Image.new("RGBA", (canvas_w, img_h), (0,0,0,0))
    draw = ImageDraw.Draw(img)

    base_color = hex_to_rgb(color)
    stroke_w = 8 if outline == "strong" else (4 if outline == "normal" else 0)

    for i, row in enumerate(lines):
        text, offset = row['text'], row['offset']
        y = padding_v + i * lh

        bbox = draw.textbbox((0,0), text, font=base_font)
        line_w = bbox[2] - bbox[0]
        x = (canvas_w - line_w) // 2

        for ci, ch in enumerate(text):
            abs_idx = offset + ci
            seg = next((s for s in segments if abs_idx >= s['start'] and abs_idx < s['end']), None)
            char_color = hex_to_rgb(seg['color']) if seg and seg.get('color') else base_color

            seg_font_path = find_korean_font(seg['font']) if seg and seg.get('font') else None
            try:
                ch_font = ImageFont.truetype(seg_font_path, font_size) if seg_font_path else base_font
            except Exception:
                ch_font = base_font

            if stroke_w > 0:
                stroke_color = hex_to_rgb(seg['strokeColor']) if seg and seg.get('strokeColor') and seg['strokeColor'] != 'none' else (0,0,0,255)
                if seg and seg.get('strokeColor') == 'none':
                    pass
                else:
                    for dx in range(-stroke_w, stroke_w+1):
                        for dy in range(-stroke_w, stroke_w+1):
                            if abs(dx)+abs(dy) >= stroke_w:
                                draw.text((x+dx, y+dy), ch, font=ch_font, fill=stroke_color)

            draw.text((x, y), ch, font=ch_font, fill=char_color)

            ch_bbox = draw.textbbox((0,0), ch, font=ch_font)
            x += ch_bbox[2] - ch_bbox[0]

    tmp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    img.save(tmp.name, "PNG")
    tmp.close()
    return tmp.name, img_h


# ── Video cutting ──

def cut_video(video_path: str, segments: list, output_path: str) -> bool:
    """구간 자르기 + 세로(9:16) 변환 1패스로 처리."""
    total_w, total_h = 1080, 1920
    video_h = 1080
    video_y = 420

    scale_filter = (
        f"scale=w={total_w}:h={video_h}:force_original_aspect_ratio=decrease,"
        f"pad={total_w}:{video_h}:(ow-iw)/2:(oh-ih)/2:black"
    )

    try:
        if len(segments) == 1:
            seg = segments[0]
            duration = seg["end"] - seg["start"]
            filter_str = (
                f"[0:v]{scale_filter}[vid];"
                f"color=black:s={total_w}x{total_h}:r=30:d={duration}[bg];"
                f"[bg][vid]overlay=0:{video_y}[out]"
            )
            result = subprocess.run([
                "ffmpeg", "-y",
                "-ss", str(seg["start"]),
                "-i", video_path,
                "-t", str(duration),
                "-filter_complex", filter_str,
                "-map", "[out]", "-map", "0:a?",
                "-c:v", "libx264", "-crf", "16", "-c:a", "aac", "-preset", "fast",
                "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
                "-color_trc", "bt709", "-color_range", "1",
                output_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        else:
            tmp_dir = tempfile.mkdtemp()
            concat_txt = os.path.join(tmp_dir, "concat.txt")
            with open(concat_txt, "w") as f:
                for i, seg in enumerate(segments):
                    cp = os.path.join(tmp_dir, f"c{i}.mp4")
                    duration = seg["end"] - seg["start"]
                    filter_str = (
                        f"[0:v]{scale_filter}[vid];"
                        f"color=black:s={total_w}x{total_h}:r=30:d={duration}[bg];"
                        f"[bg][vid]overlay=0:{video_y}[out]"
                    )
                    subprocess.run([
                        "ffmpeg", "-y",
                        "-ss", str(seg["start"]),
                        "-i", video_path,
                        "-t", str(duration),
                        "-filter_complex", filter_str,
                        "-map", "[out]", "-map", "0:a?",
                        "-c:v", "libx264", "-crf", "16", "-c:a", "aac", "-preset", "fast",
                        "-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709",
                        "-color_trc", "bt709", "-color_range", "1",
                        cp
                    ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    f.write(f"file '{cp}'\n")
            result = subprocess.run([
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", concat_txt, "-c", "copy", output_path
            ], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if result.returncode != 0:
            err = result.stderr.decode(errors="ignore") if hasattr(result, 'stderr') and result.stderr else ""
            print("FFmpeg cut_video stderr:", err[:300])
        return result.returncode == 0

    except Exception as e:
        print(f"cut_video error: {e}")
        return False


def render_with_title(raw_path: str, title: str, out_path: str,
                       font_name: str = "GmarketSans Bold",
                       font_size: int = 76, color: str = "ffffff",
                       outline: str = "strong", line_h: int = 16,
                       segments: list = None) -> bool:
    title_img_path = None
    try:
        found_font = find_korean_font(font_name)
        print(f"[Font] requested: '{font_name}' -> using: '{found_font}'")

        title_img_path, title_img_h = make_title_image(
            title, font_size=font_size, font_name=font_name,
            color=color, outline=outline, line_h=line_h,
            segments=segments or []
        )
        video_y = 420
        title_y = max(20, video_y - title_img_h - 5)

        result = subprocess.run([
            "ffmpeg", "-y",
            "-i", raw_path,
            "-i", title_img_path,
            "-filter_complex", f"[0:v][1:v]overlay=0:{title_y}[out]",
            "-map", "[out]", "-map", "0:a?",
            "-c:v", "libx264", "-crf", "16", "-c:a", "aac",
            "-preset", "medium",
            "-pix_fmt", "yuv420p",
            out_path
        ], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)

        if result.returncode != 0:
            print("render_with_title stderr:", result.stderr.decode(errors="ignore")[:400])
        return result.returncode == 0
    finally:
        if title_img_path and os.path.exists(title_img_path):
            try: os.unlink(title_img_path)
            except: pass


def suggest_titles(content: str) -> list:
    """AI가 후킹 제목 5개 추천"""
    message = claude.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=500,
        messages=[{"role": "user", "content": f"""너는 틱톡/유튜브 숏폼에서 실제로 10만+ 조회수를 뽑아낸 제목 전문가야.

아래 클립 내용을 보고 후킹 제목 5개를 추천해줘. 각각 다른 패턴으로 만들어.

【클립 내용】
{content}

━━━━━━━━━━━━━━━━━━━━━━━
실제 10만+ 조회수 패턴 (각 패턴 하나씩 사용):

1) 긴급성·한정성 → "이거 모르면 평생 후회함", "지금 안 하면 진짜 늦음"
2) 숫자·구체적 결과 → "월 300 버는 사람들 공통점", "38일 만에 매출 3배"
3) 공감·감정 자극 → "열심히 하는데 왜 안 팔릴까 ㄹㅇ", "이걸 몰라서 돈 날렸음"
4) 타겟 직접 호출 → "온라인 판매자라면 무조건", "아직도 이렇게 하고 있어? ㄷㄷ"
5) 호기심·반전 → "잘 나가는 셀러들이 숨기는 것", "다들 반대로 하고 있음 ㄹㅇ"

규칙: 20자 이내, 마침표 금지, 구어체 적극 활용, "방법"/"하는 법" 절대 금지
클립 실제 내용 기반으로만 (동떨어진 제목 금지)

JSON 배열로만 답해: ["제목1","제목2","제목3","제목4","제목5"]"""}]
    )
    raw = message.content[0].text.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return json.loads(raw)
