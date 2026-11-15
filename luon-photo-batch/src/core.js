// Lõi xử lý ảnh thuần (không phụ thuộc DOM) — chạy được cả trong trình duyệt lẫn Node để kiểm thử.
// Dữ liệu: Uint8ClampedArray RGBA, kích thước w x h.

export const DEFAULTS = Object.freeze({
  exposure: 0,      // -2..2 (stop)
  contrast: 0,      // -100..100
  saturation: 0,    // -100..100
  temperature: 0,   // -100..100 (âm = lạnh, dương = ấm)
  smooth: 0,        // 0..100 làm mịn da
});

export const PRESETS = {
  'Gốc': { ...DEFAULTS },
  'Tươi sáng': { exposure: 0.3, contrast: 10, saturation: 15, temperature: 5, smooth: 0 },
  'Chân dung mềm': { exposure: 0.2, contrast: -8, saturation: -5, temperature: 8, smooth: 40 },
  'Phim ấm': { exposure: 0, contrast: 18, saturation: -10, temperature: 22, smooth: 0 },
  'Cưới trong trẻo': { exposure: 0.4, contrast: -5, saturation: -12, temperature: 3, smooth: 20 },
};

const clamp255 = (v) => (v < 0 ? 0 : v > 255 ? 255 : v);

export function normalizeSettings(s = {}) {
  const out = { ...DEFAULTS };
  for (const k of Object.keys(DEFAULTS)) {
    const v = Number(s[k]);
    if (Number.isFinite(v)) out[k] = v;
  }
  out.exposure = Math.max(-2, Math.min(2, out.exposure));
  for (const k of ['contrast', 'saturation', 'temperature']) out[k] = Math.max(-100, Math.min(100, out[k]));
  out.smooth = Math.max(0, Math.min(100, out.smooth));
  return out;
}

export function isNeutral(s) {
  const n = normalizeSettings(s);
  return Object.keys(DEFAULTS).every((k) => n[k] === DEFAULTS[k]);
}

export function lumaOf(r, g, b) { return 0.299 * r + 0.587 * g + 0.114 * b; }

/** Điều chỉnh màu theo điểm ảnh (ghi đè trực tiếp lên data). */
export function applyAdjustments(data, settings) {
  const s = normalizeSettings(settings);
  const gain = Math.pow(2, s.exposure);
  const c = 1 + s.contrast / 100;
  const sat = 1 + s.saturation / 100;
  const t = s.temperature / 100;
  const rMul = 1 + 0.25 * t, bMul = 1 - 0.25 * t;
  for (let i = 0; i < data.length; i += 4) {
    let r = data[i] * gain * rMul, g = data[i + 1] * gain, b = data[i + 2] * gain * bMul;
    r = (r - 128) * c + 128; g = (g - 128) * c + 128; b = (b - 128) * c + 128;
    const y = lumaOf(r, g, b);
    r = y + (r - y) * sat; g = y + (g - y) * sat; b = y + (b - y) * sat;
    data[i] = clamp255(r); data[i + 1] = clamp255(g); data[i + 2] = clamp255(b);
  }
  return data;
}

/** Mặt nạ da theo YCbCr (0 hoặc 1). */
export function skinMask(data, w, h) {
  const m = new Uint8Array(w * h);
  for (let p = 0, i = 0; p < m.length; p++, i += 4) {
    const r = data[i], g = data[i + 1], b = data[i + 2];
    const cb = 128 - 0.168736 * r - 0.331264 * g + 0.5 * b;
    const cr = 128 + 0.5 * r - 0.418688 * g - 0.081312 * b;
    const y = lumaOf(r, g, b);
    m[p] = (cb >= 77 && cb <= 127 && cr >= 133 && cr <= 173 && y > 40) ? 1 : 0;
  }
  return m;
}

function boxBlurChannel(src, w, h, radius) {
  const tmp = new Float32Array(w * h), out = new Float32Array(w * h);
  const win = radius * 2 + 1;
  for (let y = 0; y < h; y++) {
    let acc = 0; const row = y * w;
    for (let x = -radius; x <= radius; x++) acc += src[row + Math.min(w - 1, Math.max(0, x))];
    for (let x = 0; x < w; x++) {
      tmp[row + x] = acc / win;
      acc += src[row + Math.min(w - 1, x + radius + 1)] - src[row + Math.max(0, x - radius)];
    }
  }
  for (let x = 0; x < w; x++) {
    let acc = 0;
    for (let y = -radius; y <= radius; y++) acc += tmp[Math.min(h - 1, Math.max(0, y)) * w + x];
    for (let y = 0; y < h; y++) {
      out[y * w + x] = acc / win;
      acc += tmp[Math.min(h - 1, y + radius + 1) * w + x] - tmp[Math.max(0, y - radius) * w + x];
    }
  }
  return out;
}

/**
 * Làm mịn da giữ cạnh: trộn ảnh gốc với bản mờ, nhưng chỉ trên vùng da và
 * giảm lực tại nơi có độ chênh sáng lớn (mắt, môi, tóc) để không mất chi tiết.
 */
export function smoothSkin(data, w, h, amount) {
  const a = Math.max(0, Math.min(100, amount)) / 100;
  if (a === 0) return data;
  const radius = Math.max(2, Math.round(Math.min(w, h) / 250));
  const mask = skinMask(data, w, h);
  const soft = boxBlurChannel(mask, w, h, radius * 2); // viền mặt nạ mềm
  const chans = [0, 1, 2].map((c) => {
    const ch = new Float32Array(w * h);
    for (let p = 0, i = c; p < ch.length; p++, i += 4) ch[p] = data[i];
    return ch;
  });
  const blurred = chans.map((ch) => boxBlurChannel(ch, w, h, radius));
  const edge = 28; // ngưỡng bảo vệ cạnh
  for (let p = 0, i = 0; p < w * h; p++, i += 4) {
    const m = soft[p];
    if (m <= 0.01) continue;
    const dl = Math.abs(lumaOf(data[i], data[i + 1], data[i + 2]) - lumaOf(blurred[0][p], blurred[1][p], blurred[2][p]));
    const keep = Math.max(0, 1 - dl / edge); // 1 = vùng phẳng, 0 = cạnh mạnh
    const k = a * m * keep;
    for (let c = 0; c < 3; c++) data[i + c] = clamp255(data[i + c] * (1 - k) + blurred[c][p] * k);
  }
  return data;
}

/** Áp toàn bộ cài đặt theo thứ tự: màu rồi làm mịn da. */
export function processPixels(data, w, h, settings) {
  const s = normalizeSettings(settings);
  applyAdjustments(data, s);
  if (s.smooth > 0) smoothSkin(data, w, h, s.smooth);
  return data;
}

/** Điểm nét = phương sai Laplacian trên ảnh xám (cao = nét). */
export function sharpnessScore(data, w, h) {
  if (w < 3 || h < 3) return 0;
  const g = new Float32Array(w * h);
  for (let p = 0, i = 0; p < g.length; p++, i += 4) g[p] = lumaOf(data[i], data[i + 1], data[i + 2]);
  let sum = 0, sum2 = 0, n = 0;
  for (let y = 1; y < h - 1; y++) {
    for (let x = 1; x < w - 1; x++) {
      const p = y * w + x;
      const l = g[p - 1] + g[p + 1] + g[p - w] + g[p + w] - 4 * g[p];
      sum += l; sum2 += l * l; n++;
    }
  }
  const mean = sum / n;
  return sum2 / n - mean * mean;
}

export function meanLuma(data) {
  let s = 0; const n = data.length / 4;
  for (let i = 0; i < data.length; i += 4) s += lumaOf(data[i], data[i + 1], data[i + 2]);
  return n ? s / n : 0;
}

/** Tỉ lệ điểm ảnh cháy sáng / mất tối (để cảnh báo phơi sáng). */
export function clippingRatio(data) {
  let hi = 0, lo = 0; const n = data.length / 4;
  for (let i = 0; i < data.length; i += 4) {
    const y = lumaOf(data[i], data[i + 1], data[i + 2]);
    if (y >= 250) hi++; else if (y <= 5) lo++;
  }
  return n ? { highlights: hi / n, shadows: lo / n } : { highlights: 0, shadows: 0 };
}

/**
 * Chấm ảnh để lọc: dùng ngưỡng tương đối so với cả buổi chụp (trung vị),
 * tránh ngưỡng cứng sai với mọi loại ống kính/độ phân giải.
 * stats: [{sharp, clip:{highlights,shadows}, luma}]
 * Trả về [{flag: 'ok'|'blur'|'over'|'under', score 0..1}]
 */
export function cullShoot(stats, { blurRatio = 0.35 } = {}) {
  const sorted = stats.map((s) => s.sharp).sort((a, b) => a - b);
  const median = sorted.length ? sorted[Math.floor(sorted.length / 2)] : 0;
  const maxS = sorted.length ? sorted[sorted.length - 1] : 0;
  return stats.map((s) => {
    const score = maxS > 0 ? s.sharp / maxS : 0;
    let flag = 'ok';
    // Phơi sáng xét trước: ảnh cháy/tối hoàn toàn cũng "không nét" nên sẽ bị gắn nhầm là mờ.
    if (s.clip.highlights > 0.25) flag = 'over';
    else if (s.clip.shadows > 0.4) flag = 'under';
    else if (median > 0 && s.sharp < median * blurRatio) flag = 'blur';
    return { flag, score };
  });
}

/** Đề xuất bù phơi sáng để các khung cân bằng độ sáng với khung tham chiếu. */
export function matchExposure(refLuma, targetLuma) {
  if (refLuma <= 0 || targetLuma <= 0) return 0;
  const stops = Math.log2(refLuma / targetLuma);
  return Math.max(-2, Math.min(2, Math.round(stops * 20) / 20));
}

// ---------- ZIP (chỉ lưu, không nén) ----------
let CRC_TABLE = null;
export function crc32(bytes) {
  if (!CRC_TABLE) {
    CRC_TABLE = new Uint32Array(256);
    for (let n = 0; n < 256; n++) {
      let c = n;
      for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
      CRC_TABLE[n] = c >>> 0;
    }
  }
  let crc = 0xffffffff;
  for (let i = 0; i < bytes.length; i++) crc = CRC_TABLE[(crc ^ bytes[i]) & 0xff] ^ (crc >>> 8);
  return (crc ^ 0xffffffff) >>> 0;
}

/** files: [{name, bytes: Uint8Array}] → Uint8Array của file .zip */
export function makeZip(files, date = new Date()) {
  const enc = new TextEncoder();
  const dosTime = ((date.getHours() << 11) | (date.getMinutes() << 5) | (date.getSeconds() >> 1)) & 0xffff;
  const dosDate = (((date.getFullYear() - 1980) << 9) | ((date.getMonth() + 1) << 5) | date.getDate()) & 0xffff;
  const chunks = [], central = [];
  let offset = 0;
  const u16 = (v) => [v & 255, (v >> 8) & 255];
  const u32 = (v) => [v & 255, (v >>> 8) & 255, (v >>> 16) & 255, (v >>> 24) & 255];
  for (const f of files) {
    const name = enc.encode(f.name);
    const crc = crc32(f.bytes);
    const flags = 0x0800; // tên UTF-8
    const local = Uint8Array.from([
      ...u32(0x04034b50), ...u16(20), ...u16(flags), ...u16(0), ...u16(dosTime), ...u16(dosDate),
      ...u32(crc), ...u32(f.bytes.length), ...u32(f.bytes.length), ...u16(name.length), ...u16(0),
    ]);
    chunks.push(local, name, f.bytes);
    central.push(Uint8Array.from([
      ...u32(0x02014b50), ...u16(20), ...u16(20), ...u16(flags), ...u16(0), ...u16(dosTime), ...u16(dosDate),
      ...u32(crc), ...u32(f.bytes.length), ...u32(f.bytes.length), ...u16(name.length), ...u16(0), ...u16(0),
      ...u16(0), ...u16(0), ...u32(0), ...u32(offset),
    ]), name);
    offset += local.length + name.length + f.bytes.length;
  }
  const cdSize = central.reduce((a, c) => a + c.length, 0);
  const end = Uint8Array.from([
    ...u32(0x06054b50), ...u16(0), ...u16(0), ...u16(files.length), ...u16(files.length),
    ...u32(cdSize), ...u32(offset), ...u16(0),
  ]);
  const all = [...chunks, ...central, end];
  const out = new Uint8Array(all.reduce((a, c) => a + c.length, 0));
  let p = 0; for (const c of all) { out.set(c, p); p += c.length; }
  return out;
}

/** Tên file xuất không trùng nhau trong zip. */
export function uniqueNames(names) {
  const seen = new Map();
  return names.map((n) => {
    const dot = n.lastIndexOf('.');
    const base = dot > 0 ? n.slice(0, dot) : n, ext = dot > 0 ? n.slice(dot) : '';
    const key = n.toLowerCase();
    const c = seen.get(key) || 0; seen.set(key, c + 1);
    return c === 0 ? n : `${base}_${c + 1}${ext}`;
  });
}
