"""Actual headless GL tests: model visibility, alpha, multi-axis changes, bad inputs."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

renderer = Path(sys.argv[1])
model = Path(sys.argv[2])
with tempfile.TemporaryDirectory(prefix='ofs-renderer-check-') as tmp:
    folder = Path(tmp)
    frames = folder / 'frames.json'
    output = folder / 'model.rgba'
    frames.write_text(json.dumps([
        [.2, .5, .5, .5, .5, .2],
        [.8, .5, .5, .5, .5, .2],
        [.8, .5, .5, .5, .5, .8],
    ]))
    command = [str(renderer), '--frames-json', str(frames), '--output', str(output),
               '--width', '128', '--height', '128', '--model', str(model)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    data = output.read_bytes()
    frame_size = 128 * 128 * 4
    assert len(data) == frame_size * 3
    images = [data[i * frame_size:(i + 1) * frame_size] for i in range(3)]
    for image in images:
        alpha = image[3::4]
        assert alpha.count(0) > len(alpha) * .5, 'background must be transparent'
        assert sum(a > 0 for a in alpha) > 20, 'selected model must be visible'
    assert images[0] != images[1], 'stroke must change rendered pixels'
    assert images[1] != images[2], 'pitch must change rendered pixels even with same stroke'
    # Bad scripts are rejected before touching the output.
    frames.write_text(json.dumps([[1.1, .5, .5, .5, .5, .5]]))
    result = subprocess.run(command, capture_output=True, text=True, timeout=30)
    assert result.returncode != 0 and 'normalized' in result.stderr
    assert output.read_bytes() == data
    frames.write_text(json.dumps([[.5]*6]))
    result = subprocess.run(command + ['--unknown','1'], capture_output=True, text=True, timeout=30)
    assert result.returncode != 0
    assert output.read_bytes() == data
    # Default procedural OFS model works without any GLB dependency.
    result = subprocess.run(command[:-2], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    # Actual Pitch label/bar is independently visible outside the model bounds.
    frames.write_text(json.dumps([[.8,.5,.5,.5,.5,.2],[.8,.5,.5,.5,.5,.8]]))
    hud_command=[str(renderer),'--frames-json',str(frames),'--output',str(output),
                 '--width','480','--height','480','--axes-present','stroke,pitch']
    result=subprocess.run(hud_command,capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    hud=output.read_bytes()
    size=480*480*4
    def region(data,index):
        image=data[index*size:(index+1)*size]
        return b''.join(image[(y*480+370)*4:(y*480+460)*4] for y in range(220,260))
    assert region(hud,0)!=region(hud,1),'Pitch 20/80 must change the numeric label pixels'
    assert sum(a>0 for a in region(hud,0)[3::4])>150,'Pitch label must visibly render'
    result=subprocess.run(hud_command+['--no-axis-hud'],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    plain=output.read_bytes()
    assert not any(region(plain,0)[3::4]),'HUD must disappear when explicitly disabled'
    for extra in (['--axes-present','wrong'],['--axes-present','pitch,pitch'],
                  ['--hud-text-scale','0.6']):
        result=subprocess.run(hud_command[: -2]+extra,capture_output=True,text=True,timeout=30)
        assert result.returncode!=0
        assert output.read_bytes()==plain,'Invalid HUD options must preserve existing output'
print('offscreen GLB/builtin, alpha, stroke+pitch, actual Pitch HUD, validation: passed')
