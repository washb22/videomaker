import os, json, base64, time
from openai import OpenAI

TEMP = 'temp'

MAX_RETRIES = 3
RETRY_DELAYS = [3, 5, 10]  # 재시도 대기 시간 (초)


def generate_images(session_id: str, style: str = '사실적인 사진', images_per_scene: int = 2) -> dict:
    """장면별 이미지 생성 (gpt-image-1.5) - 재시도 + 이어하기 지원"""
    session_dir = os.path.join(TEMP, session_id)
    scenes_path = os.path.join(session_dir, 'scenes.json')
    images_dir = os.path.join(session_dir, 'images')
    images_json_path = os.path.join(session_dir, 'images.json')
    os.makedirs(images_dir, exist_ok=True)

    with open(scenes_path, 'r', encoding='utf-8') as f:
        scenes = json.load(f)

    # 이어하기: 이미 생성된 이미지 확인
    existing = {}
    if os.path.exists(images_json_path):
        with open(images_json_path, 'r', encoding='utf-8') as f:
            prev_data = json.load(f)
        for item in prev_data:
            if item.get('success') and item.get('paths'):
                # 파일이 실제로 존재하는지 확인
                valid_paths = [p for p in item['paths'] if p and os.path.exists(p)]
                if len(valid_paths) >= images_per_scene:
                    existing[item['scene_index']] = item

    client = OpenAI(api_key=os.environ.get('OPENAI_API_KEY'))

    style_map = {
        '사실적인 사진': 'photorealistic, high quality photograph',
        '수채화': 'watercolor illustration, artistic',
        '애니메이션': 'anime style, colorful illustration',
        '미니멀': 'minimalist flat design illustration',
        '3D 렌더': '3D rendered, cinematic lighting'
    }
    style_prompt = style_map.get(style, style)

    generated = []
    total_scenes = len(scenes)

    for scene_num, scene in enumerate(scenes):
        idx = scene['index']

        # 이어하기: 이미 완성된 장면은 스킵
        if idx in existing:
            generated.append(existing[idx])
            print(f"[이미지] 장면 {idx} 스킵 (이미 생성됨) [{scene_num+1}/{total_scenes}]")
            continue

        desc = scene.get('image_description', f"장면 {idx}")
        narration = scene.get('narration', '')

        scene_images = []
        for img_idx in range(images_per_scene):
            if img_idx == 0:
                prompt_desc = desc
            else:
                prompt_desc = f"{desc}, different angle and composition, showing another perspective of the scene"

            full_prompt = f"{prompt_desc}, {style_prompt}, no text, no watermark, 16:9 ratio"
            img_path = os.path.join(images_dir, f'scene_{idx:03d}_{img_idx}.png')

            # 이미 파일이 있으면 스킵
            if os.path.exists(img_path) and os.path.getsize(img_path) > 0:
                scene_images.append({'path': img_path, 'success': True})
                continue

            # 재시도 로직
            success = False
            for attempt in range(MAX_RETRIES):
                try:
                    response = client.images.generate(
                        model="gpt-image-1.5",
                        prompt=full_prompt,
                        size="1536x1024",
                        quality="low",
                        n=1
                    )

                    img_obj = response.data[0]
                    if hasattr(img_obj, 'b64_json') and img_obj.b64_json:
                        img_data = base64.b64decode(img_obj.b64_json)
                    elif hasattr(img_obj, 'url') and img_obj.url:
                        import requests
                        img_data = requests.get(img_obj.url, timeout=30).content
                    else:
                        raise Exception("이미지 데이터 없음")

                    with open(img_path, 'wb') as f:
                        f.write(img_data)

                    scene_images.append({'path': img_path, 'success': True})
                    success = True
                    break

                except Exception as e:
                    error_msg = str(e)
                    if attempt < MAX_RETRIES - 1:
                        delay = RETRY_DELAYS[attempt]
                        print(f"[이미지] 장면 {idx} 이미지 {img_idx} 실패 (시도 {attempt+1}/{MAX_RETRIES}): {error_msg[:80]}. {delay}초 후 재시도...")
                        time.sleep(delay)
                    else:
                        print(f"[이미지] 장면 {idx} 이미지 {img_idx} 최종 실패: {error_msg[:80]}")
                        scene_images.append({
                            'path': None,
                            'success': False,
                            'error': error_msg
                        })

        successful = [img for img in scene_images if img['success']]
        generated.append({
            'scene_index': idx,
            'path': successful[0]['path'] if successful else None,
            'paths': [img['path'] for img in successful],
            'description': desc,
            'success': len(successful) > 0,
            'image_count': len(successful)
        })

        print(f"[이미지] 장면 {idx} 완료 ({len(successful)}/{images_per_scene}장) [{scene_num+1}/{total_scenes}]")

        # 매 장면마다 중간 저장 (중단 시 이어하기 가능)
        with open(images_json_path, 'w', encoding='utf-8') as f:
            json.dump(generated, f, ensure_ascii=False, indent=2)

    total_imgs = sum(g['image_count'] for g in generated)
    failed = [g for g in generated if not g['success']]

    result = {
        'generated': len([g for g in generated if g['success']]),
        'failed': len(failed),
        'total': len(scenes),
        'total_images': total_imgs,
        'images': generated
    }

    if failed:
        result['failed_scenes'] = [g['scene_index'] for g in failed]
        print(f"[이미지] 경고: {len(failed)}개 장면 실패 - {result['failed_scenes']}")

    return result
