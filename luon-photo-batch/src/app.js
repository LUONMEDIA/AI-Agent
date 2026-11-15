import { DEFAULTS, PRESETS, normalizeSettings, isNeutral, processPixels, sharpnessScore, meanLuma,
  clippingRatio, cullShoot, matchExposure, makeZip, uniqueNames } from './core.js';

const $ = (id) => document.getElementById(id);
const PREVIEW_SIDE = 1100, THUMB_SIDE = 240, ANALYZE_SIDE = 400;
const BADGE = { ok: 'Tốt', blur: 'Mờ', over: 'Cháy sáng', under: 'Quá tối' };
const SLIDERS = [
  ['exposure', 'Phơi sáng', -2, 2, 0.05], ['contrast', 'Tương phản', -100, 100, 1],
  ['saturation', 'Bão hòa', -100, 100, 1], ['temperature', 'Nhiệt độ màu', -100, 100, 1],
  ['smooth', 'Làm mịn da', 0, 100, 1],
];

/** @type {{name:string,file:File,bitmap:ImageBitmap,preview:HTMLCanvasElement,settings:object,selected:boolean,flag:string,luma:number,el:HTMLElement}[]} */
let items = [];
let current = -1;
let draft = { ...DEFAULTS };
let raf = 0;

function scaled(bitmap, side) {
  const k = Math.min(1, side / Math.max(bitmap.width, bitmap.height));
  const c = document.createElement('canvas');
  c.width = Math.max(1, Math.round(bitmap.width * k)); c.height = Math.max(1, Math.round(bitmap.height * k));
  c.getContext('2d').drawImage(bitmap, 0, 0, c.width, c.height);
  return c;
}
function render(srcCanvas, settings, target) {
  target.width = srcCanvas.width; target.height = srcCanvas.height;
  const ctx = target.getContext('2d', { willReadFrequently: true });
  ctx.drawImage(srcCanvas, 0, 0);
  if (!isNeutral(settings)) {
    const img = ctx.getImageData(0, 0, target.width, target.height);
    processPixels(img.data, img.width, img.height, settings);
    ctx.putImageData(img, 0, 0);
  }
}
const setStatus = (t) => { $('status').textContent = t; };

// ---- nạp ảnh ----
async function addFiles(fileList) {
  const files = [...fileList].filter((f) => /^image\/(jpeg|png|webp)$/.test(f.type));
  const skipped = fileList.length - files.length;
  if (!files.length) { setStatus('Không có ảnh JPG/PNG/WebP hợp lệ.'); return; }
  $('workspace').hidden = false;
  for (let i = 0; i < files.length; i++) {
    setStatus(`Đang đọc ảnh ${i + 1}/${files.length}…`);
    try {
      const bitmap = await createImageBitmap(files[i], { imageOrientation: 'from-image' });
      const preview = scaled(bitmap, PREVIEW_SIDE);
      const it = { name: files[i].name, file: files[i], bitmap, preview, settings: { ...DEFAULTS }, selected: false, flag: 'ok', luma: 0, el: null };
      const small = scaled(bitmap, ANALYZE_SIDE);
      const an = small.getContext('2d').getImageData(0, 0, small.width, small.height);
      it.sharp = sharpnessScore(an.data, an.width, an.height); it.clip = clippingRatio(an.data); it.luma = meanLuma(an.data);
      items.push(it); buildThumb(it);
    } catch { setStatus(`Không đọc được ${files[i].name}`); }
    if (i % 3 === 2) await new Promise((r) => setTimeout(r));
  }
  recull();
  if (current < 0 && items.length) select(0);
  setStatus(`Đã nạp ${items.length} ảnh.` + (skipped ? ` Bỏ qua ${skipped} file không hỗ trợ.` : ''));
}
function recull() {
  const res = cullShoot(items.map((i) => ({ sharp: i.sharp, clip: i.clip, luma: i.luma })));
  items.forEach((it, i) => { it.flag = res[i].flag; const b = it.el.querySelector('.badge'); b.textContent = BADGE[it.flag]; b.className = `badge ${it.flag}`; });
}
function buildThumb(it) {
  const el = document.createElement('div'); el.className = 'thumb';
  const c = document.createElement('canvas'); const t = scaled(it.bitmap, THUMB_SIDE);
  c.width = t.width; c.height = t.height; c.getContext('2d').drawImage(t, 0, 0); it.thumbSrc = t; it.thumb = c;
  const badge = document.createElement('span'); badge.className = 'badge'; badge.textContent = '…';
  const name = document.createElement('div'); name.className = 'name'; name.textContent = it.name; name.title = it.name;
  el.append(c, badge, name);
  el.addEventListener('click', (e) => { if (e.metaKey || e.ctrlKey || e.shiftKey) { it.selected = !it.selected; refreshThumbs(); } else select(items.indexOf(it)); });
  it.el = el; $('grid').append(el);
}
function refreshThumbs() { items.forEach((it, i) => { it.el.classList.toggle('current', i === current); it.el.classList.toggle('selected', it.selected); }); }

// ---- xem & chỉnh ----
function select(i) {
  current = i; draft = { ...DEFAULTS, ...items[i].settings };
  $('target-label').textContent = `· ${items[i].name}`;
  syncSliders(); drawMain(); refreshThumbs();
}
function drawMain() { if (current >= 0) render(items[current].preview, draft, $('main')); }
function scheduleDraw() { cancelAnimationFrame(raf); raf = requestAnimationFrame(() => { drawMain(); commitDraft(); }); }
function commitDraft() {
  if (current < 0) return; const it = items[current]; it.settings = normalizeSettings(draft);
  render(it.thumbSrc, it.settings, it.thumb);
}
function buildSliders() {
  const box = $('sliders');
  for (const [key, label, min, max, step] of SLIDERS) {
    const l = document.createElement('label'); const o = document.createElement('output'); o.id = `o-${key}`;
    const r = document.createElement('input'); Object.assign(r, { type: 'range', min, max, step, id: `s-${key}`, value: DEFAULTS[key] });
    r.addEventListener('input', () => { draft[key] = Number(r.value); o.textContent = r.value; scheduleDraw(); });
    r.addEventListener('dblclick', () => { r.value = DEFAULTS[key]; r.dispatchEvent(new Event('input')); });
    l.append(`${label}: `, o, r); box.append(l);
  }
}
function syncSliders() { for (const [key] of SLIDERS) { $(`s-${key}`).value = draft[key]; $(`o-${key}`).textContent = draft[key]; } }
function buildPresets() {
  const sel = $('preset');
  for (const name of Object.keys(PRESETS)) sel.append(new Option(name, name));
  sel.addEventListener('change', () => { draft = { ...PRESETS[sel.value] }; syncSliders(); scheduleDraw(); });
}
function targets(all) { return all ? items : items.filter((i) => i.selected); }
function applyTo(list) {
  if (!list.length) { setStatus('Chưa chọn ảnh nào (Ctrl/⌘/Shift + bấm để chọn nhiều ảnh).'); return; }
  const s = normalizeSettings(draft);
  for (const it of list) { it.settings = { ...s }; render(it.thumbSrc, it.settings, it.thumb); }
  setStatus(`Đã áp dụng cho ${list.length} ảnh.`);
}

// ---- xuất ----
async function exportZip() {
  if (!items.length) return;
  const btn = $('export'), prog = $('progress');
  const q = Number($('quality').value) / 100, maxSide = Math.max(0, Number($('maxside').value) || 0);
  btn.disabled = true; prog.hidden = false; prog.value = 0;
  const files = [], names = uniqueNames(items.map((i) => i.name.replace(/\.[^.]+$/, '') + '.jpg'));
  try {
    for (let i = 0; i < items.length; i++) {
      const it = items[i]; setStatus(`Đang xuất ${i + 1}/${items.length}: ${it.name}`);
      const k = maxSide ? Math.min(1, maxSide / Math.max(it.bitmap.width, it.bitmap.height)) : 1;
      const c = document.createElement('canvas'); c.width = Math.round(it.bitmap.width * k); c.height = Math.round(it.bitmap.height * k);
      const ctx = c.getContext('2d', { willReadFrequently: true }); ctx.drawImage(it.bitmap, 0, 0, c.width, c.height);
      if (!isNeutral(it.settings)) { const img = ctx.getImageData(0, 0, c.width, c.height); processPixels(img.data, c.width, c.height, it.settings); ctx.putImageData(img, 0, 0); }
      const blob = await new Promise((res) => c.toBlob(res, 'image/jpeg', q));
      if (!blob) throw new Error(`Không mã hóa được ${it.name} (ảnh quá lớn?)`);
      files.push({ name: names[i], bytes: new Uint8Array(await blob.arrayBuffer()) });
      prog.value = (i + 1) / items.length; c.width = c.height = 0;
    }
    const zip = new Blob([makeZip(files)], { type: 'application/zip' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(zip); a.download = `luon-photo-batch-${new Date().toISOString().slice(0, 10)}.zip`;
    document.body.append(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(a.href), 10000);
    setStatus(`Xong: ${files.length} ảnh trong file ZIP.`);
  } catch (e) { setStatus(`Lỗi: ${e.message}`); }
  finally { btn.disabled = false; prog.hidden = true; }
}

// ---- sự kiện ----
buildSliders(); buildPresets();
$('file').addEventListener('change', (e) => addFiles(e.target.files));
const drop = $('drop');
['dragenter', 'dragover'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => addFiles(e.dataTransfer.files));
window.addEventListener('dragover', (e) => e.preventDefault()); window.addEventListener('drop', (e) => { e.preventDefault(); if (!drop.contains(e.target)) addFiles(e.dataTransfer.files); });
$('apply-sel').onclick = () => applyTo(targets(false));
$('apply-all').onclick = () => applyTo(targets(true));
$('reset').onclick = () => { if (current < 0) return; draft = { ...DEFAULTS }; $('preset').value = 'Gốc'; syncSliders(); scheduleDraw(); };
$('match-exp').onclick = () => {
  if (current < 0) return; const ref = items[current].luma, list = items.filter((i) => i.selected && i !== items[current]);
  if (!list.length) { setStatus('Chọn các ảnh cần cân sáng (Ctrl/⌘/Shift + bấm), rồi bấm lại.'); return; }
  for (const it of list) { it.settings = normalizeSettings({ ...it.settings, exposure: matchExposure(ref, it.luma) }); render(it.thumbSrc, it.settings, it.thumb); }
  setStatus(`Đã cân độ sáng ${list.length} ảnh theo ảnh đang xem; các chỉnh khác của từng ảnh được giữ nguyên.`);
};
$('select-ok').onclick = () => { items.forEach((i) => { i.selected = i.flag === 'ok'; }); refreshThumbs(); setStatus(`Đã chọn ${items.filter((i) => i.selected).length} ảnh tốt.`); };
$('remove-bad').onclick = () => {
  const bad = items.filter((i) => i.flag !== 'ok');
  if (!bad.length) { setStatus('Không có ảnh nào bị gắn cờ.'); return; }
  if (!confirm(`Bỏ ${bad.length} ảnh bị gắn cờ khỏi danh sách xuất? (File gốc trên máy không bị xóa)`)) return;
  const curItem = items[current];
  bad.forEach((i) => { i.el.remove(); i.bitmap.close?.(); });
  items = items.filter((i) => i.flag === 'ok'); recull();
  current = items.indexOf(curItem); if (current < 0 && items.length) select(0); else refreshThumbs();
  if (!items.length) { current = -1; const c = $('main'); c.width = c.height = 0; }
  setStatus(`Đã bỏ ${bad.length} ảnh khỏi danh sách.`);
};
$('quality').addEventListener('input', (e) => { $('q-out').textContent = e.target.value; });
$('export').onclick = exportZip;
const main = $('main'), hold = (on) => { if (current >= 0) render(items[current].preview, on ? DEFAULTS : draft, main); };
main.addEventListener('pointerdown', () => hold(true)); window.addEventListener('pointerup', () => hold(false));
