import os, json, subprocess, requests, time

TEMP = 'temp'

MAX_RETRIES = 3
RETRY_DELAYS = [3, 5, 10]


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
    """장면별 나레이션 TTS 생성 - 재시도 + 이어하기 지원"""
    session_dir = os.path.join(TEMP, session_id)
    scenes_path = os.path.join(session_dir, 'scenes.json')
    audio_dir = os.path.join(session_dir, 'audio')
    audio_json_path = os.path.join(session_dir, 'audio.json')
    os.makedirs(audio_dir, exist_ok=True)

    with open(scenes_path, 'r', encoding='utf-8') as f:
        scenes = json.load(f)

    # 이어하기: 이미 생성된 TTS 확인
    existing = {}
    if os.path.exists(audio_json_path):
        with open(audio_json_path, 'r', encoding='utf-8') as f:
            prev_data = json.load(f)
        for item in prev_data:
            if item.get('success') and item.get('path') and os.path.exists(item['path']):
                existing[item['scene_index']] = item

    api_key = os.environ.get('ELEVENLABS_API_KEY')
    total_scenes = len(scenes)

    generated = []
    for scene_num, scene in enumerate(scenes):
        narration = scene.get('narration', '').strip()
        if not narration:
            continue

        idx = scene['index']

        # 이어하기: 이미 완성된 장면 스킵
        if idx in existing:
            generated.append(existing[idx])
            print(f"[TTS] 장면 {idx} 스킵 (이미 생성됨) [{scene_num+1}/{total_scenes}]")
            continue

        audio_path = os.path.join(audio_dir, f'scene_{idx:03d}.mp3')

        # 재시도 로직
        success = False
        for attempt in range(MAX_RETRIES):
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
                response = requests.post(
                    url,
                    headers=headers,
                    data=json.dumps(payload, ensure_ascii=False).encode('utf-8'),
                    timeout=120
                )

                if response.status_code == 200:
                    with open(audio_path, 'wb') as f:
                        f.write(response.content)
                    duration = _get_audio_duration(audio_path)
                    generated.append({
                        'scene_index': idx,
                        'path': audio_path,
                        'duration': duration,
                        'success': True
                    })
                    success = True
                    print(f"[TTS] 장면 {idx} 완료 ({duration:.1f}초) [{scene_num+1}/{total_scenes}]")
                    break
                elif response.status_code == 429:
                    # 레이트 제한 → 더 오래 대기
                    delay = RETRY_DELAYS[attempt] * 2
                    print(f"[TTS] 장면 {idx} 레이트 제한 (시도 {attempt+1}/{MAX_RETRIES}). {delay}초 후 재시도...")
                    time.sleep(delay)
                else:
                    raise Exception(f"ElevenLabs 오류: {response.status_code} {response.text[:100]}")

            except Exception as e:
                error_msg = str(e)
                if attempt < MAX_RETRIES - 1:
                    delay = RETRY_DELAYS[attempt]
                    print(f"[TTS] 장면 {idx} 실패 (시도 {attempt+1}/{MAX_RETRIES}): {error_msg[:80]}. {delay}초 후 재시도...")
                    time.sleep(delay)
                else:
                    print(f"[TTS] 장면 {idx} 최종 실패: {error_msg[:80]}")
                    generated.append({
                        'scene_index': idx,
                        'path': None,
                        'success': False,
                        'error': error_msg
                    })

        # 매 장면마다 중간 저장
        with open(audio_json_path, 'w', encoding='utf-8') as f:
            json.dump(generated, f, ensure_ascii=False, indent=2)

    failed = [g for g in generated if not g['success']]
    result = {
        'generated': len([g for g in generated if g['success']]),
        'failed': len(failed),
        'total': len(scenes),
        'audio': generated
    }

    if failed:
        result['failed_scenes'] = [g['scene_index'] for g in failed]
        print(f"[TTS] 경고: {len(failed)}개 장면 실패 - {result['failed_scenes']}")

    return result
