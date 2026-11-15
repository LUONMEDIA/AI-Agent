import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import * as C from '../src/core.js';

const px = (r, g, b, n = 4) => { const d = new Uint8ClampedArray(n * 4); for (let i = 0; i < n; i++) d.set([r, g, b, 255], i * 4); return d; };
let passed = 0; const t = (name, fn) => { fn(); passed++; console.log('ok -', name); };

t('trung tính không đổi ảnh', () => {
  const d = px(100, 150, 200); C.processPixels(d, 2, 2, {});
  assert.deepEqual([...d.slice(0, 3)], [100, 150, 200]);
});
t('phơi sáng +1 stop nhân đôi', () => {
  const d = px(50, 60, 70); C.applyAdjustments(d, { exposure: 1 });
  assert.deepEqual([...d.slice(0, 3)], [100, 120, 140]);
});
t('giá trị lạ bị kẹp và NaN bị bỏ', () => {
  const s = C.normalizeSettings({ exposure: 99, contrast: 'abc', smooth: -5 });
  assert.equal(s.exposure, 2); assert.equal(s.contrast, 0); assert.equal(s.smooth, 0);
});
t('bão hòa -100 ra ảnh xám', () => {
  const d = px(200, 100, 50); C.applyAdjustments(d, { saturation: -100 });
  assert.ok(Math.abs(d[0] - d[1]) <= 1 && Math.abs(d[1] - d[2]) <= 1);
});
t('nhiệt độ ấm tăng đỏ giảm xanh', () => {
  const d = px(128, 128, 128); C.applyAdjustments(d, { temperature: 100 });
  assert.ok(d[0] > 128 && d[2] < 128);
});
t('độ nét: ảnh caro > ảnh phẳng', () => {
  const w = 16, h = 16, a = new Uint8ClampedArray(w * h * 4), f = px(128, 128, 128, w * h);
  for (let y = 0; y < h; y++) for (let x = 0; x < w; x++) { const v = (x + y) % 2 ? 255 : 0; a.set([v, v, v, 255], (y * w + x) * 4); }
  assert.ok(C.sharpnessScore(a, w, h) > C.sharpnessScore(f, w, h) + 1000);
});
t('lọc ảnh: gắn cờ mờ theo trung vị buổi chụp', () => {
  const ok = { sharp: 100, clip: { highlights: 0, shadows: 0 } };
  const r = C.cullShoot([ok, ok, ok, { ...ok, sharp: 5 }, { ...ok, clip: { highlights: 0.5, shadows: 0 } }]);
  assert.equal(r[3].flag, 'blur'); assert.equal(r[4].flag, 'over'); assert.equal(r[0].flag, 'ok');
});
t('lọc ảnh: ảnh cháy hoàn toàn gắn cờ cháy, không phải mờ', () => {
  const ok = { sharp: 100, clip: { highlights: 0, shadows: 0 } };
  const r = C.cullShoot([ok, ok, ok, { sharp: 0, clip: { highlights: 1, shadows: 0 } }, { sharp: 0, clip: { highlights: 0, shadows: 1 } }]);
  assert.equal(r[3].flag, 'over'); assert.equal(r[4].flag, 'under');
});
t('cân phơi sáng khớp khung tham chiếu', () => {
  assert.equal(C.matchExposure(100, 50), 1); assert.equal(C.matchExposure(100, 100), 0); assert.equal(C.matchExposure(0, 50), 0);
});
t('làm mịn da giảm nhiễu vùng da, giữ nguyên vùng không phải da', () => {
  const w = 24, h = 24, d = new Uint8ClampedArray(w * h * 4);
  for (let p = 0; p < w * h; p++) { const n = (p * 37) % 9 - 4; d.set([200 + n, 150 + n, 130 + n, 255], p * 4); } // da có nhiễu
  const v = (a) => { let s = 0, s2 = 0; for (let p = 0; p < w * h; p++) { s += a[p * 4]; s2 += a[p * 4] ** 2; } const m = s / (w * h); return s2 / (w * h) - m * m; };
  const before = v(d); C.smoothSkin(d, w, h, 100); assert.ok(v(d) < before * 0.7, 'phương sai phải giảm');
  const blue = px(20, 40, 200, w * h); C.smoothSkin(blue, w, h, 100); assert.deepEqual([...blue.slice(0, 3)], [20, 40, 200]);
});
t('tên xuất không trùng', () => {
  assert.deepEqual(C.uniqueNames(['a.jpg', 'A.jpg', 'b.jpg', 'a.jpg']), ['a.jpg', 'A_2.jpg', 'b.jpg', 'a_3.jpg']);
});
t('crc32 chuẩn', () => assert.equal(C.crc32(new TextEncoder().encode('123456789')), 0xcbf43926));
t('zip hợp lệ (giải nén bằng unzip, so khớp nội dung và tên UTF-8)', () => {
  const dir = mkdtempSync(join(tmpdir(), 'zip-'));
  try {
    const files = [{ name: 'ảnh 1.txt', bytes: new TextEncoder().encode('xin chào') }, { name: 'b.bin', bytes: Uint8Array.from([0, 1, 2, 255]) }];
    writeFileSync(join(dir, 't.zip'), C.makeZip(files));
    const list = execFileSync('unzip', ['-l', join(dir, 't.zip')]).toString();
    assert.ok(list.includes('b.bin'));
    execFileSync('unzip', ['-tq', join(dir, 't.zip')]);
    assert.equal(execFileSync('unzip', ['-p', join(dir, 't.zip'), 'b.bin']).toString('hex'), '000102ff');
  } finally { rmSync(dir, { recursive: true, force: true }); }
});
console.log(`\n${passed} test đạt`);
