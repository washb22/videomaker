import os, json, base64
from openai import OpenAI

TEMP = 'temp'

def generate_images(session_id: str, style: str = '사실적인 사진', images_per_scene: int = 2) -> dict:
    """장면별 이미지 생성 (gpt-image-1.5) - 장면당 여러 장 지원"""
    session_dir = os.path.join(TEMP, session_id)
    scenes_path = os.path.join(session_dir, 'scenes.json')
    images_dir = os.path.join(session_dir, 'images')
    os.makedirs(images_dir, exist_ok=True)

    with open(scenes_path, 'r', encoding='utf-8') as f:
        scenes = json.load(f)

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
    for scene in scenes:
        desc = scene.get('image_description', f"장면 {scene['index']}")
        narration = scene.get('narration', '')

        scene_images = []
        for img_idx in range(images_per_scene):
            if img_idx == 0:
                prompt_desc = desc
            else:
                prompt_desc = f"{desc}, different angle and composition, showing another perspective of the scene"

            full_prompt = f"{prompt_desc}, {style_prompt}, no text, no watermark, 16:9 ratio"

            try:
                response = client.images.generate(
                    model="gpt-image-1.5",
                    prompt=full_prompt,
                    size="1536x1024",
                    quality="medium",
                    n=1
                )

                img_path = os.path.join(images_dir, f'scene_{scene["index"]:03d}_{img_idx}.png')

                # gpt-image 모델은 b64_json 또는 url로 반환
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

                scene_images.append({
                    'path': img_path,
                    'success': True
                })
            except Exception as e:
                scene_images.append({
                    'path': None,
                    'success': False,
                    'error': str(e)
                })

        successful = [img for img in scene_images if img['success']]
        generated.append({
            'scene_index': scene['index'],
            'path': successful[0]['path'] if successful else None,
            'paths': [img['path'] for img in successful],
            'description': desc,
            'success': len(successful) > 0,
            'image_count': len(successful)
        })

    with open(os.path.join(session_dir, 'images.json'), 'w', encoding='utf-8') as f:
        json.dump(generated, f, ensure_ascii=False, indent=2)

    total_imgs = sum(g['image_count'] for g in generated)
    return {
        'generated': len([g for g in generated if g['success']]),
        'total': len(scenes),
        'total_images': total_imgs,
        'images': generated
    }
