import os, json, subprocess

TEMP = 'temp'

def remove_silence(session_id: str) -> dict:
    """TTS 오디오에서 무음 구간 자동 제거"""
    session_dir = os.path.join(TEMP, session_id)
    audio_path = os.path.join(session_dir, 'audio.json')
    processed_dir = os.path.join(session_dir, 'audio_processed')
    os.makedirs(processed_dir, exist_ok=True)

    with open(audio_path, 'r', encoding='utf-8') as f:
        audio_data = json.load(f)

    processed = []
    for item in audio_data:
        if not item['success'] or not item['path']:
            processed.append(item)
            continue

        input_path = item['path']
        output_path = os.path.join(processed_dir, os.path.basename(input_path))

        try:
            # FFmpeg silenceremove 필터로 무음 제거
            # stop_periods=-1 : 오디오 전체에서 모든 무음 구간 제거 (1로 하면 첫 문장 후 잘림)
            cmd = [
                'ffmpeg', '-y',
                '-i', input_path,
                '-af', 'silenceremove=start_periods=1:start_silence=0.3:start_threshold=-40dB:stop_periods=-1:stop_silence=0.3:stop_threshold=-40dB',
                output_path
            ]
            subprocess.run(cmd, capture_output=True, check=True, timeout=60)

            # 길이 측정
            duration = get_audio_duration(output_path)

            processed.append({
                'scene_index': item['scene_index'],
                'path': output_path,
                'duration': duration,
                'success': True
            })
        except Exception as e:
            # FFmpeg 없으면 원본 그대로
            duration = get_audio_duration(input_path)
            processed.append({
                'scene_index': item['scene_index'],
                'path': input_path,
                'duration': duration,
                'success': True,
                'note': f'무음제거 스킵: {str(e)}'
            })

    with open(os.path.join(session_dir, 'audio_processed.json'), 'w', encoding='utf-8') as f:
        json.dump(processed, f, ensure_ascii=False, indent=2)

    return {
        'processed': len([p for p in processed if p['success']]),
        'total': len(audio_data),
        'audio': processed
    }


def get_audio_duration(path: str) -> float:
    """오디오 길이 측정 (초)"""
    try:
        cmd = [
            'ffprobe', '-v', 'quiet',
            '-print_format', 'json',
            '-show_format',
            path
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        data = json.loads(result.stdout)
        return float(data['format']['duration'])
    except:
        return 5.0  # 기본값
