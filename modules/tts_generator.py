import os, json, subprocess, requests

TEMP = 'temp'

def get_voices() -> list:
    """ElevenLabs에서 전체 목소리 목록 가져오기"""
    api_key = os.environ.get('ELEVENLABS_API_KEY')
    try:
        res = requests.get(
            "https://api.elevenlabs.io/v1/voices",
            headers={"xi-api-key": api_key},
            timeout=10
        )
        data = res.json()
        voices = []
        for v in data.get('voices', []):
            labels = v.get('labels', {})
            desc = []
            if labels.get('gender'): desc.append(labels['gender'])
            if labels.get('age'): desc.append(labels['age'])
            if labels.get('accent'): desc.append(labels['accent'])
            voices.append({
                'voice_id': v['voice_id'],
                'name': v['name'],
                'category': v.get('category', 'general'),
                'description': ' · '.join(desc) if desc else '',
                'preview_url': v.get('preview_url', '')
            })
        voices.sort(key=lambda x: x['name'])
        return voices
    except Exception as e:
        return [
            {'voice_id': 'EXAVITQu4vr4xnSDxMaL', 'name': 'Rachel', 'category': 'premade', 'description': 'female', 'preview_url': ''},
            {'voice_id': 'pNInz6obpgDQGcFmaJgB', 'name': 'Adam', 'category': 'premade', 'description': 'male', 'preview_url': ''},
            {'voice_id': 'MF3mGyEYCl7XYWbV9V6O', 'name': 'Elli', 'category': 'premade', 'description': 'female', 'preview_url': ''},
            {'voice_id': 'ErXwobaYiN019PkySvjV', 'name': 'Antoni', 'category': 'premade', 'description': 'male', 'preview_url': ''},
        ]


def _get_audio_duration(path: str) -> float:
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
    except Exception:
        return 5.0


def generate_tts(session_id: str, voice_id: str = 'EXAVITQu4vr4xnSDxMaL') -> dict:
    """장면별 나레이션 TTS 생성"""
    session_dir = os.path.join(TEMP, session_id)
    scenes_path = os.path.join(session_dir, 'scenes.json')
    audio_dir = os.path.join(session_dir, 'audio')
    os.makedirs(audio_dir, exist_ok=True)

    with open(scenes_path, 'r', encoding='utf-8') as f:
        scenes = json.load(f)

    api_key = os.environ.get('ELEVENLABS_API_KEY')

    generated = []
    for scene in scenes:
        narration = scene.get('narration', '').strip()
        if not narration:
            continue

        audio_path = os.path.join(audio_dir, f'scene_{scene["index"]:03d}.mp3')

        try:
            url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
            headers = {
                "Accept": "audio/mpeg",
                "Content-Type": "application/json; charset=utf-8",
                "xi-api-key": api_key
            }
            payload = {
                "text": narration,
                "model_id": "eleven_multilingual_v2",
                "voice_settings": {
                    "stability": 0.5,
                    "similarity_boost": 0.75
                }
            }
            # ★ 핵심 수정: 한글 인코딩 문제 해결 - json= 대신 data= 로 UTF-8 직접 인코딩
            response = requests.post(
                url,
                headers=headers,
                data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                timeout=60
            )

            if response.status_code == 200:
                with open(audio_path, 'wb') as f:
                    f.write(response.content)
                # duration 측정 (silence_remover 건너뛸 경우 대비)
                duration = _get_audio_duration(audio_path)
                generated.append({
                    'scene_index': scene['index'],
                    'path': audio_path,
                    'duration': duration,
                    'success': True
                })
            else:
                generated.append({
                    'scene_index': scene['index'],
                    'path': None,
                    'success': False,
                    'error': f"ElevenLabs 오류: {response.status_code} {response.text[:100]}"
                })
        except Exception as e:
            generated.append({
                'scene_index': scene['index'],
                'path': None,
                'success': False,
                'error': str(e)
            })

    with open(os.path.join(session_dir, 'audio.json'), 'w', encoding='utf-8') as f:
        json.dump(generated, f, ensure_ascii=False, indent=2)

    return {
        'generated': len([g for g in generated if g['success']]),
        'total': len(scenes),
        'audio': generated
    }
