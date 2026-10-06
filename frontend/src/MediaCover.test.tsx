// @vitest-environment jsdom
import { createRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Mock } from 'vitest';
import { act, cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { SourceVideo, parseVideoTime, videoTime } from './SourceVideo';
import { CoverEditor, coverCrop } from './CoverEditor';
import type { CoverEditorHandle, CoverState } from './CoverEditor';
import type { Work } from './api';

const work = { id: 7, script_id: 'S070', title: '作品', assets: [{ id: 9, name: 'main.mp4', kind: 'video' }] } as Work;
const initial: CoverState = { work_id: 7, mode: 'manual', revision: 4, cover_url: '/api/covers/7?v=old', video_asset_id: 9, time_seconds: 1, crop: { x: 0, y: 0, width: 1, height: 1 } };
const frame = { frame_id: '1234567890abcdef1234567890abcdef', frame_url: '/api/works/7/cover/frames/1234567890abcdef1234567890abcdef', width: 1920, height: 1080, video_asset_id: 9, time_seconds: 72.5 };
const response = (value: unknown, status=200) => new Response(JSON.stringify(value), { status, headers: { 'Content-Type': 'application/json' } });
let fetchMock: Mock<(url: string, init?: RequestInit) => Promise<Response>>;
let drawImage: ReturnType<typeof vi.fn>;
beforeEach(() => {
  drawImage = vi.fn();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({ drawImage } as unknown as CanvasRenderingContext2D);
  vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  Object.defineProperty(HTMLDialogElement.prototype, 'showModal', { configurable: true, value: function(this: HTMLDialogElement) { this.open = true; } });
  Object.defineProperty(HTMLDialogElement.prototype, 'close', { configurable: true, value: function(this: HTMLDialogElement) { this.open = false; } });
  fetchMock = vi.fn(async (url, init) => response(url.endsWith('/frames') ? frame : init?.method === 'POST' ? { ...initial, revision: 5, cover_url: '/api/covers/7?v=new' } : initial));
  vi.stubGlobal('fetch', fetchMock);
});
afterEach(() => { cleanup(); vi.restoreAllMocks(); vi.unstubAllGlobals(); });

describe('source video', () => {
  it('parses seconds and clock times precisely and rejects invalid or out-of-range components', () => {
    expect(parseVideoTime('72.5')).toBe(72.5);
    expect(parseVideoTime('01:12.500')).toBe(72.5);
    expect(parseVideoTime('1:02:03.125')).toBe(3723.125);
    for (const value of ['-1', '00:60:00', '01:60', 'NaN', 'Infinity', '1e3', '', '1:2:3:4']) expect(parseVideoTime(value)).toBeNull();
    expect(videoTime(72.5)).toBe('00:01:12.500');
    expect(videoTime(59.9998)).toBe('00:01:00.000');
  });
  it('uses the real host-only media URL, native manual playback and validated seek positioning', () => {
    const selected = vi.fn();
    const view = render(<SourceVideo workId={7} assetId={9} supported onTimeSelected={selected} />);
    const video = view.container.querySelector('video')!;
    expect(video.getAttribute('src')).toBe('/api/works/7/assets/9/media');
    expect(video.controls).toBe(true); expect(video.autoplay).toBe(false);
    expect(video.getAttribute('preload')).toBe('metadata');
    Object.defineProperty(video, 'duration', { configurable: true, value: 100 });
    Object.defineProperty(video, 'readyState', { configurable: true, value: 1 });
    fireEvent.change(screen.getByLabelText('时间位置'), { target: { value: '00:01:12.500' } });
    fireEvent.click(screen.getByRole('button', { name: '定位时间' }));
    expect(video.currentTime).toBe(72.5); expect(selected).toHaveBeenLastCalledWith(72.5);
    expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledOnce();
    fireEvent.seeked(video);
    expect((screen.getByLabelText('时间位置') as HTMLInputElement).value).toBe('00:01:12.500');
    fireEvent.change(screen.getByLabelText('时间位置'), { target: { value: '100' } });
    fireEvent.keyDown(screen.getByLabelText('时间位置'), { key: 'Enter' });
    expect(screen.getByRole('alert').textContent).toContain('视频时长范围');
    expect(video.currentTime).toBe(72.5);
  });
  it('omits playback on LAN and provides a directory fallback for browser decode failures', () => {
    const open = vi.fn();
    const view = render(<SourceVideo workId={7} assetId={9} supported={false} onOpenFolder={open} />);
    expect(view.container.querySelector('video')).toBeNull();
    expect(screen.getByText('原视频播放仅在素材所在本机可用。')).toBeTruthy();
    view.rerender(<SourceVideo workId={7} assetId={9} supported onOpenFolder={open} />);
    fireEvent.error(view.container.querySelector('video')!);
    fireEvent.click(screen.getByRole('button', { name: '打开所在目录' }));
    expect(open).toHaveBeenCalledOnce();
    const selected = vi.fn();
    view.rerender(<SourceVideo workId={7} assetId={9} supported onOpenFolder={open} onTimeSelected={selected} />);
    fireEvent.change(screen.getByLabelText('时间位置'), { target: { value: '5.125' } });
    fireEvent.click(screen.getByRole('button', { name: '定位时间' }));
    expect(selected).toHaveBeenCalledWith(5.125);
  });
});

const editor = () => {
  const close = vi.fn(), saved = vi.fn(), ref = createRef<CoverEditorHandle>();
  const view = render(<CoverEditor ref={ref} work={work} onClose={close} onSaved={saved} onOpenFolder={() => {}} />);
  return { ...view, close, saved, ref };
};
const capture = async () => {
  await waitFor(() => expect((screen.getByRole('button', { name: '截取当前画面' }) as HTMLButtonElement).disabled).toBe(false));
  fireEvent.change(screen.getByLabelText('时间位置'), { target: { value: '00:01:12.500' } });
  fireEvent.click(screen.getByRole('button', { name: '定位时间' }));
  fireEvent.click(screen.getByRole('button', { name: '截取当前画面' }));
  const image = await screen.findByRole('img', { name: '截取的视频画面' });
  fireEvent.load(image);
  return image;
};

describe('cover editor', () => {
  it('keeps every normalized crop inside the frame and physically at 16:9', () => {
    for (const [width,height] of [[1920,1080],[1080,1920],[1000,1000],[2560,1080]]) {
      const crop = coverCrop(width,height,3,0,1);
      expect(crop.x).toBeGreaterThanOrEqual(0); expect(crop.y).toBeGreaterThanOrEqual(0);
      expect(crop.x+crop.width).toBeLessThanOrEqual(1);
      expect(crop.y+crop.height).toBeLessThanOrEqual(1);
      expect(crop.width*width/(crop.height*height)).toBeCloseTo(16/9,6);
    }
  });
  it('captures the selected time and video ID and sends the same live canvas crop with revision CAS', async () => {
    const { saved } = editor();
    const image = await capture();
    const framePost = fetchMock.mock.calls.find(([url,init]) => url.endsWith('/frames') && init?.method==='POST')!;
    expect(JSON.parse(String(framePost[1]?.body))).toEqual({ video_asset_id:9,time_seconds:72.5 });
    fireEvent.change(screen.getByLabelText('缩放'),{target:{value:'2'}});
    fireEvent.change(screen.getByLabelText('水平位置'),{target:{value:'.7'}});
    fireEvent.change(screen.getByLabelText('垂直位置'),{target:{value:'.4'}});
    expect(parseFloat((image as HTMLImageElement).style.width)).toBe(200);
    expect(parseFloat((image as HTMLImageElement).style.left)).toBeCloseTo(-90,5);
    const args=drawImage.mock.calls.at(-1)!;
    expect(args[0]).toBe(image);
    expect(args.slice(1,5)).toEqual([expect.closeTo(864,5),expect.closeTo(162,5),960,540]);
    expect(args.slice(5)).toEqual([0,0,1280,720]);
    fireEvent.click(screen.getByRole('button',{name:'保存封面'}));
    await waitFor(()=>expect(saved).toHaveBeenCalledOnce());
    const post=fetchMock.mock.calls.find(([url,init])=>url==='/api/works/7/cover'&&init?.method==='POST')!;
    expect(JSON.parse(String(post[1]?.body))).toEqual({ expected_revision:4,frame_id:frame.frame_id,crop:{x:expect.closeTo(.45,8),y:expect.closeTo(.15,8),width:.5,height:.5} });
    await capture();
    fireEvent.click(screen.getByRole('button',{name:'保存封面'}));
    await waitFor(()=>expect(saved).toHaveBeenCalledTimes(2));
    const latest=fetchMock.mock.calls.filter(([url,init])=>url==='/api/works/7/cover'&&init?.method==='POST').at(-1)!;
    expect(JSON.parse(String(latest[1]?.body)).expected_revision).toBe(5);
  });
  it('keeps the captured image and crop after a 409 until the cover state is re-read', async () => {
    const {saved}=editor(); const image=await capture();
    fireEvent.change(screen.getByLabelText('缩放'),{target:{value:'2'}});
    fetchMock.mockResolvedValueOnce(response({detail:'封面已被其他操作更新'},409));
    fireEvent.click(screen.getByRole('button',{name:'保存封面'}));
    await screen.findByText('封面已被其他操作更新');
    expect(saved).not.toHaveBeenCalled(); expect(screen.getByRole('img',{name:'截取的视频画面'})).toBe(image);
    expect((screen.getByLabelText('缩放')as HTMLInputElement).value).toBe('2');
    expect((screen.getByRole('button',{name:'保存封面'})as HTMLButtonElement).disabled).toBe(true);
    expect((screen.getByRole('button',{name:'截取当前画面'})as HTMLButtonElement).disabled).toBe(true);
    fireEvent.click(screen.getByRole('button',{name:'重新读取封面状态'}));
    await waitFor(()=>expect((screen.getByRole('button',{name:'保存封面'})as HTMLButtonElement).disabled).toBe(false));
  });
  it('guards parent navigation, Escape and closing when there is a captured crop and preserves it when continuing', async () => {
    const {close,ref}=editor(); const image=await capture(); const next=vi.fn();
    act(()=>ref.current!.requestLeave(next));
    expect(next).not.toHaveBeenCalled(); expect(screen.getByText('封面修改尚未保存')).toBeTruthy();
    fireEvent.click(screen.getByRole('button',{name:'继续编辑'}));
    expect(screen.getByRole('img',{name:'截取的视频画面'})).toBe(image);
    fireEvent(screen.getByRole('dialog'),new Event('cancel',{cancelable:true}));
    expect(close).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button',{name:'放弃封面修改'}));
    expect(close).toHaveBeenCalledOnce(); expect(next).not.toHaveBeenCalled();
  });
  it('does not leave or submit twice while capture is running and preserves manual state after restore fails', async () => {
    const {close,saved,ref}=editor();
    await waitFor(()=>expect((screen.getByRole('button',{name:'截取当前画面'})as HTMLButtonElement).disabled).toBe(false));
    fetchMock.mockResolvedValueOnce(response({detail:'没有可用视频，无法恢复自动封面'},422));
    fireEvent.click(screen.getByRole('button',{name:'恢复自动封面'}));
    await screen.findByText('没有可用视频，无法恢复自动封面');
    expect(saved).not.toHaveBeenCalled();
    expect(screen.getByRole('button',{name:'恢复自动封面'})).toBeTruthy();
    let finish:((value:Response)=>void)|undefined;
    const original=fetchMock.getMockImplementation()!;
    fetchMock.mockImplementation((url,init)=>url.endsWith('/frames')&&init?.method==='POST'?new Promise<Response>(resolve=>{finish=resolve;}):original(url,init));
    fireEvent.click(screen.getByRole('button',{name:'截取当前画面'}));
    fireEvent.click(screen.getByRole('button',{name:'截取当前画面'}));
    act(()=>ref.current!.requestLeave(close));
    expect(close).not.toHaveBeenCalled();
    expect(fetchMock.mock.calls.filter(([url,init])=>url.endsWith('/frames')&&init?.method==='POST')).toHaveLength(1);
    await act(async()=>finish!(response(frame)));
    expect(screen.getByRole('img',{name:'截取的视频画面'})).toBeTruthy();
  });
});