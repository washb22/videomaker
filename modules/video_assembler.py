import os, json, re, subprocess

TEMP = 'temp'
OUTPUT = 'output'

def assemble_video(session_id: str, add_subtitle: bool = True, subtitle_style: str = 'default') -> dict:
    """이미지 + 오디오 + 자막 합쳐서 최종 영상 제작"""
    session_dir = os.path.join(TEMP, session_id)
    os.makedirs(OUTPUT, exist_ok=True)

    # 데이터 로드
    images_path = os.path.join(session_dir, 'images.json')
    audio_path_file = os.path.join(session_dir, 'audio_processed.json')
    scenes_path = os.path.join(session_dir, 'scenes.json')

    if not os.path.exists(audio_path_file):
        audio_path_file = os.path.join(session_dir, 'audio.json')

    with open(images_path, 'r', encoding='utf-8') as f:
        images = json.load(f)
    with open(audio_path_file, 'r', encoding='utf-8') as f:
        audios = json.load(f)
    with open(scenes_path, 'r', encoding='utf-8') as f:
        scenes = json.load(f)

    image_map = {img['scene_index']: img for img in images if img['success']}
    audio_map = {aud['scene_index']: aud for aud in audios if aud['success']}

    clip_list_path = os.path.join(session_dir, 'clip_list.txt')
    subtitle_path = os.path.join(session_dir, 'subtitles.srt')

    clip_files = []
    srt_entries = []
    current_time = 0.0

    for scene in scenes:
        idx = scene['index']
        img_info = image_map.get(idx)
        aud_info = audio_map.get(idx)

        if not img_info or not aud_info:
            continue

        aud_path = aud_info['path']
        duration = aud_info.get('duration', 5.0)

        # 이미지 목록: 여러 장이면 paths, 아니면 단일 path
        img_paths = img_info.get('paths', [img_info['path']])
        img_paths = [p for p in img_paths if p and os.path.exists(p)]
        if not img_paths:
            continue

        num_imgs = len(img_paths)

        if num_imgs >= 2:
            # 여러 이미지 → 각각 서브클립 만들어서 concat 후 오디오 합성
            sub_dur = duration / num_imgs
            sub_clips = []
            for si, ip in enumerate(img_paths):
                sub_path = os.path.join(session_dir, f'sub_{idx:03d}_{si}.mp4')
                frames = max(int(sub_dur * 25), 25)
                cmd = [
                    'ffmpeg', '-y',
                    '-loop', '1', '-i', ip,
                    '-t', str(sub_dur),
                    '-vf', f'scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,zoompan=z=\'min(zoom+0.001,1.3)\':d={frames}:s=1920x1080',
                    '-c:v', 'libx264', '-r', '25', '-pix_fmt', 'yuv420p',
                    '-an', sub_path
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    # fallback: 단순 스케일
                    cmd_s = [
                        'ffmpeg', '-y',
                        '-loop', '1', '-i', ip,
                        '-t', str(sub_dur),
                        '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2',
                        '-c:v', 'libx264', '-r', '25', '-pix_fmt', 'yuv420p',
                        '-an', sub_path
                    ]
                    subprocess.run(cmd_s, capture_output=True, timeout=120)
                sub_clips.append(sub_path)

            # 서브클립 concat
            sub_list_path = os.path.join(session_dir, f'sublist_{idx:03d}.txt')
            with open(sub_list_path, 'w', encoding='utf-8') as f:
                for sc in sub_clips:
                    f.write(f"file '{os.path.abspath(sc).replace(chr(92), '/')}'\n")

            video_only = os.path.join(session_dir, f'vidonly_{idx:03d}.mp4')
            subprocess.run([
                'ffmpeg', '-y', '-f', 'concat', '-safe', '0',
                '-i', sub_list_path, '-c', 'copy', video_only
            ], capture_output=True, timeout=120)

            # 오디오 합성
            clip_path = os.path.join(session_dir, f'clip_{idx:03d}.mp4')
            subprocess.run([
                'ffmpeg', '-y',
                '-i', video_only, '-i', aud_path,
                '-c:v', 'copy', '-c:a', 'aac', '-shortest',
                clip_path
            ], capture_output=True, timeout=120)

        else:
            # 단일 이미지 (기존 방식)
            img_path = img_paths[0]
            clip_path = os.path.join(session_dir, f'clip_{idx:03d}.mp4')
            try:
                cmd = [
                    'ffmpeg', '-y',
                    '-loop', '1', '-i', img_path,
                    '-i', aud_path,
                    '-filter_complex',
                    f'[0:v]scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,zoompan=z=\'min(zoom+0.0008,1.3)\':d={int(duration*25)}:s=1920x1080[v]',
                    '-map', '[v]', '-map', '1:a',
                    '-c:v', 'libx264', '-c:a', 'aac',
                    '-shortest', '-t', str(duration), '-r', '25',
                    clip_path
                ]
                result = subprocess.run(cmd, capture_output=True, timeout=120)
                if result.returncode != 0:
                    cmd_simple = [
                        'ffmpeg', '-y',
                        '-loop', '1', '-i', img_path,
                        '-i', aud_path,
                        '-c:v', 'libx264', '-c:a', 'aac',
                        '-shortest', '-t', str(duration), '-r', '25',
                        '-vf', 'scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2',
                        clip_path
                    ]
                    subprocess.run(cmd_simple, capture_output=True, timeout=120, check=True)
            except Exception as e:
                print(f"클립 {idx} 생성 실패: {e}")
                continue

        clip_files.append(clip_path)

        # SRT 자막 생성 - 문장 단위 분할
        if add_subtitle:
            narration = scene.get('narration', '')
            sentences = split_sentences(narration)
            if sentences:
                total_chars = sum(len(s) for s in sentences)
                t = current_time
                for sent in sentences:
                    sent_dur = duration * (len(sent) / total_chars) if total_chars > 0 else duration / len(sentences)
                    srt_entries.append({
                        'start': t,
                        'end': t + sent_dur,
                        'text': sent
                    })
                    t += sent_dur

        current_time += duration

    if not clip_files:
        raise Exception("생성된 클립이 없습니다")

    # 클립 리스트 파일 작성
    with open(clip_list_path, 'w', encoding='utf-8') as f:
        for cp in clip_files:
            safe_path = os.path.abspath(cp).replace('\\', '/')
            f.write(f"file '{safe_path}'\n")

    # SRT 파일 저장
    if add_subtitle and srt_entries:
        with open(subtitle_path, 'w', encoding='utf-8') as f:
            for i, entry in enumerate(srt_entries, 1):
                wrapped = wrap_subtitle(entry['text'])
                f.write(f"{i}\n")
                f.write(f"{seconds_to_srt(entry['start'])} --> {seconds_to_srt(entry['end'])}\n")
                f.write(f"{wrapped}\n\n")

    # 클립 합치기
    merged_path = os.path.join(session_dir, 'merged.mp4')
    merge_cmd = [
        'ffmpeg', '-y',
        '-f', 'concat', '-safe', '0',
        '-i', clip_list_path,
        '-c', 'copy',
        merged_path
    ]
    subprocess.run(merge_cmd, capture_output=True, timeout=300, check=True)

    # 자막 삽입
    final_path = os.path.join(OUTPUT, f'{session_id}_final.mp4')

    if add_subtitle and os.path.exists(subtitle_path):
        style = get_subtitle_style(subtitle_style)
        safe_sub_path = subtitle_path.replace('\\', '/').replace(':', '\\:')
        subtitle_cmd = [
            'ffmpeg', '-y',
            '-i', merged_path,
            '-vf', f"subtitles={safe_sub_path}:force_style='{style}'",
            '-c:a', 'copy',
            final_path
        ]
        result = subprocess.run(subtitle_cmd, capture_output=True, timeout=300)
        if result.returncode != 0:
            import shutil
            shutil.copy2(merged_path, final_path)
    else:
        import shutil
        shutil.copy2(merged_path, final_path)

    file_size = os.path.getsize(final_path) / (1024 * 1024)

    return {
        'output_path': final_path,
        'file_size_mb': round(file_size, 1),
        'total_duration': round(current_time, 1),
        'clip_count': len(clip_files)
    }


def split_sentences(text: str) -> list:
    """나레이션을 자막 한 줄 단위로 분할"""
    parts = re.split(r'(?<=[.?!。？！])\s*', text)
    sentences = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        if len(p) > 40:
            sub = re.split(r'(?<=,)\s+|(?<=，)\s*|(?<=요)\s+(?=.)|(?<=다)\s+(?=.)', p)
            for s in sub:
                s = s.strip()
                if s:
                    sentences.append(s)
        else:
            sentences.append(p)
    return sentences if sentences else [text]


def wrap_subtitle(text: str, max_line: int = 22) -> str:
    """자막 텍스트를 최대 2줄로 자연스럽게 줄바꿈.

    - 한 줄에 max_line자 이하면 그대로 반환
    - 2줄 필요시 쉼표 뒤 > 공백 위치 중 중앙에 가장 가까운 곳에서 분할
    - 단어/숫자 중간에서 절대 안 자름
    """
    text = text.strip()
    if len(text) <= max_line:
        return text

    mid = len(text) // 2

    # 1순위: 쉼표+공백 뒤에서 분할 (중앙에서 가까운 순)
    # 2순위: 공백에서 분할
    best_pos = -1
    best_dist = len(text)

    for i in range(len(text)):
        # 쉼표 바로 뒤 (쉼표+공백 패턴) - 숫자 사이 쉼표(1,000) 제외
        if text[i] == ',' and i + 1 < len(text):
            # 앞뒤가 숫자면 천단위 구분자이므로 건너뜀
            if i > 0 and text[i-1].isdigit() and text[i+1].isdigit():
                continue
            pos = i + 2 if i + 1 < len(text) and text[i + 1] == ' ' else i + 1
            dist = abs(pos - mid)
            if dist < best_dist:
                best_dist = dist
                best_pos = pos

    # 쉼표 분할점이 너무 치우치면 (한쪽이 8자 미만) 공백으로 대체
    if best_pos != -1 and (best_pos < 8 or len(text) - best_pos < 8):
        best_pos = -1
        best_dist = len(text)

    # 쉼표 분할점 못 찾으면 공백에서 분할
    if best_pos == -1:
        for i in range(len(text)):
            if text[i] == ' ':
                pos = i
                dist = abs(pos - mid)
                if dist < best_dist:
                    best_dist = dist
                    best_pos = pos

    if best_pos > 0:
        line1 = text[:best_pos].strip()
        line2 = text[best_pos:].strip()
        return f"{line1}\n{line2}"

    return text


def seconds_to_srt(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    ms = int((seconds % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def get_subtitle_style(style: str) -> str:
    styles = {
        'default': 'FontName=Arial,FontSize=24,PrimaryColour=&HFFFFFF,OutlineColour=&H000000,Outline=2,Bold=1',
        'yellow': 'FontName=Arial,FontSize=28,PrimaryColour=&H00FFFF,OutlineColour=&H000000,Outline=2,Bold=1',
        'black_bg': 'FontName=Arial,FontSize=24,PrimaryColour=&HFFFFFF,BackColour=&H80000000,BorderStyle=4',
        'minimal': 'FontName=Arial,FontSize=20,PrimaryColour=&HFFFFFF,Outline=1',
    }
    return styles.get(style, styles['default'])
