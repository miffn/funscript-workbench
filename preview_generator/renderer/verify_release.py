"""Independently decode all release outputs and compare absolute-time model pixels.

Usage: python -m preview_generator.renderer.verify_release /path/to/manifest.json
This writes diagnostics only to the workbench's ignored data directory.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

from preview_generator.scripts import frame_values, load_script

manifest_path = Path(sys.argv[1])
manifest = json.loads(manifest_path.read_text())
assert manifest['status'] == 'completed'
assert set(manifest['scripts']) == {'stroke', 'pitch'}, 'S064 must use both scripts'
assert len(manifest['outputs']) == 8
root = Path(__file__).resolve().parents[2]
folder = root / 'data/preview-generator-check'
folder.mkdir(parents=True, exist_ok=True)
report = {'source_sha256_unchanged': {}, 'outputs': [], 'absolute_time_overlay': []}

def run(args):
    return subprocess.run([str(a) for a in args], capture_output=True, check=True).stdout

for name, item in {'video': manifest['video'], **manifest['scripts']}.items():
    h = hashlib.sha256()
    with Path(item['path']).open('rb') as handle:
        while chunk := handle.read(4*1024*1024):
            h.update(chunk)
    assert h.hexdigest() == item['sha256'], f'Source changed: {name}'
    report['source_sha256_unchanged'][name] = h.hexdigest()
for item in manifest['outputs']:
    path = Path(item['path'])
    raw = run(['ffprobe','-v','error','-count_frames','-show_streams','-show_format','-of','json',path])
    info = json.loads(raw)
    stream = next(s for s in info['streams'] if s['codec_type'] == 'video')
    wanted = 300 if item['kind'] == 'video' else 100
    assert int(stream['nb_read_frames']) == wanted, (path,stream['nb_read_frames'])
    assert stream['width'] == item['width'] and stream['height'] == item['height']
    assert stream['avg_frame_rate'] == ('30/1' if item['kind']=='video' else '10/1')
    run(['ffmpeg','-nostdin','-v','error','-i',path,'-f','null','-'])
    if item['kind']=='gif':
        assert not any(s['codec_type']=='audio' for s in info['streams'])
    else:
        assert any(s.get('codec_name')=='opus' for s in info['streams'])
    report['outputs'].append({'filename':item['filename'], 'frames':wanted, 'decoded':True,
                              'dimensions':[item['width'],item['height']]})

scripts = {axis:load_script(Path(item['path'])) for axis,item in manifest['scripts'].items()}
# Re-render the simulator independently at the absolute start of each clip.
width, height = manifest['options']['simulator_width'], manifest['options']['simulator_height']
values = [frame_values(scripts,clip['start_seconds'],1,30)[0] for clip in manifest['clips']]
(folder/'reference-axes.json').write_text(json.dumps(values))
renderer_command=[root/'preview_generator/build/ofs-preview-renderer','--frames-json',folder/'reference-axes.json',
     '--output',folder/'reference.rgba','--width',width,'--height',height,
     '--axes-present',','.join(manifest['hud']['axes_present']),
     '--hud-text-scale',manifest['hud']['text_scale']]
if not manifest['hud']['enabled']:
    renderer_command += ['--no-axis-hud']
if not manifest['model'].get('builtin'):
    renderer_command += ['--model',manifest['model']['path']]
for option in ('pitch_range','camera_yaw','camera_pitch','camera_distance','camera_fov','model_scale'):
    renderer_command += ['--'+option.replace('_','-'),manifest['options'][option]]
run(renderer_command)
references = (folder/'reference.rgba').read_bytes()
frame_size=width*height*4
for i,clip in enumerate(manifest['clips']):
    path=manifest_path.parent/f"预览视频{clip['index']}.webm"
    margin=manifest['options']['margin']
    x=manifest['options']['width']-width-margin
    y=manifest['options']['height']-height-margin
    decoded=run(['ffmpeg','-nostdin','-v','error','-i',path,'-vf',f'crop={width}:{height}:{x}:{y}',
                 '-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
    ref=references[i*frame_size:(i+1)*frame_size]
    errors=[]
    for pixel in range(width*height):
        if ref[pixel*4+3]==255:
            errors.extend(abs(decoded[pixel*3+c]-ref[pixel*4+c]) for c in range(3))
    assert len(errors)>300, 'Simulator must visibly occur in composed video'
    mae=sum(errors)/len(errors)
    # VP9 + YUV420 introduces color/chroma losses around tiny colored details.
    assert mae<35, f'Wrong simulator motion/time/overlay position: MAE={mae}'
    report['absolute_time_overlay'].append({'clip':clip['index'],'start_seconds':clip['start_seconds'],
        'stroke':values[i][0],'pitch':values[i][5],'opaque_samples':len(errors)//3,
        'mean_absolute_rgb_error':round(mae,3)})
(folder/'S064-verification-corrected.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps(report,ensure_ascii=False,indent=2))

