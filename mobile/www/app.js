/* فاحص الأجهزة — منطق التطبيق. البلجن الأصلي: DeviceDoctor (انظر native/) */
(function () {
  'use strict';
  var $ = function (s, r) { return (r || document).querySelector(s); };
  var DD = null;
  try {
    var C = window.Capacitor;
    DD = C && ((C.Plugins && C.Plugins.DeviceDoctor) || (C.registerPlugin && C.registerPlugin('DeviceDoctor'))) || null;
  } catch (e) { DD = null; }

  var ICON = { ok: '✔', warn: '⚠', bad: '✖', info: 'ℹ' };
  var R = new Map();
  try { var saved = JSON.parse(localStorage.getItem('dd_results') || '[]'); saved.forEach(function (x) { R.set(x[0], x[1]); }); } catch (e) {}

  function setR(key, name, status, details, fix) {
    R.set(key, { name: name, status: status, details: details || '', fix: fix || '' });
    try { localStorage.setItem('dd_results', JSON.stringify(Array.from(R.entries()))); } catch (e) {}
    render();
  }
  function fmt(b) {
    if (!b) return '?';
    var u = ['B', 'KB', 'MB', 'GB', 'TB'], i = 0;
    while (b >= 1024 && i < 4) { b /= 1024; i++; }
    return b.toFixed(i > 1 ? 1 : 0) + ' ' + u[i];
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }

  function render() {
    var arr = Array.from(R.values()), cnt = { ok: 0, warn: 0, bad: 0 };
    arr.forEach(function (r) { if (cnt[r.status] !== undefined) cnt[r.status]++; });
    $('#summary').innerHTML = '<span style="color:#4ade80">✔ ' + cnt.ok + '</span><span style="color:#facc15">⚠ ' + cnt.warn + '</span><span style="color:#f87171">✖ ' + cnt.bad + '</span>';
    var order = { bad: 0, warn: 1, info: 2, ok: 3 };
    arr.sort(function (a, b) { return order[a.status] - order[b.status]; });
    $('#list').innerHTML = arr.length ? arr.map(function (r) {
      return '<div class="card ' + r.status + '"><b>' + ICON[r.status] + ' ' + esc(r.name) + '</b><div class="d">' + esc(r.details) + '</div>' +
        (r.fix ? '<div class="f">الحل: ' + esc(r.fix) + '</div>' : '') + '</div>';
    }).join('') : '<p class="hint">ما فيه نتائج بعد. اعمل الفحص السريع أو الاختبارات اليدوية.</p>';
    $('#txt').value = arr.map(function (r) {
      return '[' + ICON[r.status] + '] ' + r.name + ': ' + r.details + (r.fix ? ' — الحل: ' + r.fix : '');
    }).join('\n');
  }

  /* ───────── التبويبات ───────── */
  document.querySelectorAll('.tab').forEach(function (t) {
    t.onclick = function () {
      document.querySelectorAll('.tab').forEach(function (x) { x.classList.toggle('on', x === t); });
      document.querySelectorAll('.pane').forEach(function (p) { p.classList.toggle('on', p.id === t.dataset.t); });
    };
  });
  function goReport() { document.querySelector('[data-t=report]').click(); }

  /* ───────── الفحص السريع ───────── */
  var HEALTH = { 1: 'غير معروفة', 2: 'جيدة', 3: 'سخونة زائدة', 4: 'تالفة', 5: 'فولتية زائدة', 6: 'عطل غير محدد', 7: 'باردة جدًا' };
  var STYPES = { 1: ['accel', 'مقياس التسارع', 'bad'], 2: ['mag', 'البوصلة', 'warn'], 4: ['gyro', 'الجيروسكوب', 'warn'], 5: ['light', 'حساس الإضاءة', 'warn'], 8: ['prox', 'حساس القرب', 'warn'] };

  async function quick(silent) {
    var btn = $('#runQuick');
    btn.disabled = true; btn.textContent = '... جاري الفحص';
    if (!DD) setR('native', 'الوصول للنظام', 'warn', 'التطبيق يعمل بدون الجزء الأصلي (غالبًا فتحته من المتصفح).', 'ثبّت نسخة APK الأصلية.');
    else {
      try { evalBattery(await DD.getBattery(), LOCAL); } catch (e) { setR('battery', 'البطارية', 'info', 'تعذّرت القراءة'); }
      try { evalBatteryDeep(await DD.batteryDeep(), LOCAL); } catch (e) { /* غير مدعوم */ }
      try { evalSystem(await DD.getSystem(), LOCAL); } catch (e) { setR('system', 'النظام', 'info', 'تعذّرت القراءة'); }
      try { evalSensors((await DD.getSensors()).sensors, LOCAL); } catch (e) { setR('sensorlist', 'الحساسات', 'info', 'تعذّرت القراءة'); }
    }
    await networkCheck();
    await mediaCount();
    setR('screen', 'معلومات الشاشة', 'info', screen.width + '×' + screen.height + ' | كثافة ' + (window.devicePixelRatio || 1) + ' | نقاط لمس ' + (navigator.maxTouchPoints || 0));
    btn.disabled = false; btn.textContent = '▶ أعد الفحص السريع';
    if (!silent) goReport();
  }

  var LOCAL = { k: '', n: '' };

  function evalBattery(b, P) {
    if (!b || !b.available) return setR(P.k + 'battery', P.n + 'البطارية', 'warn', 'ما تم اكتشاف بطارية');
    var st = 'ok', fix = '';
    if ([3, 4, 5, 6].indexOf(b.health) >= 0) { st = 'bad'; fix = 'البطارية معطوبة أو تسخن؛ افصل الشاحن واستبدلها.'; }
    else if ([1, 7].indexOf(b.health) >= 0) st = 'warn';
    if (b.temperature >= 45) { st = 'bad'; fix = 'الحرارة عالية جدًا؛ افصل الشاحن وأبعد الجهاز عن الحرارة.'; }
    else if (b.temperature >= 40 && st === 'ok') { st = 'warn'; fix = 'الحرارة مرتفعة نسبيًا.'; }
    if (b.present === false) { st = 'bad'; fix = 'النظام ما يشوف البطارية.'; }
    var d = 'الشحن ' + b.level + '% | الحالة: ' + (HEALTH[b.health] || b.health) + ' | الحرارة ' + b.temperature.toFixed(1) + '°C | الفولتية ' + b.voltage + ' mV' + (b.technology ? ' | ' + b.technology : '');
    setR(P.k + 'battery', P.n + 'البطارية', st, d, fix);
    if (typeof b.currentNow === 'number' && b.currentNow !== 0) setR(P.k + 'bcur', P.n + 'تيار البطارية', 'info', 'التيار الحالي ≈ ' + Math.round(Math.abs(b.currentNow) / (Math.abs(b.currentNow) > 20000 ? 1000 : 1)) + ' mA (' + (b.currentNow > 0 ? 'يشحن' : 'يستهلك') + ')');
    if (b.chargeCounter > 0 && b.level > 10) {
      var cap = Math.round(b.chargeCounter / 1000 * 100 / b.level);
      setR(P.k + 'bcap', P.n + 'سعة البطارية التقديرية', 'info', 'حوالي ' + cap + ' mAh عند الحالة الحالية (قارنها بالسعة المكتوبة على البطارية؛ إذا أقل من 75% منها فالبطارية ضعيفة)');
    }
    if (b.cycleCount > 0) {
      var s2 = b.cycleCount > 800 ? 'bad' : b.cycleCount > 500 ? 'warn' : 'ok';
      setR(P.k + 'cycles', P.n + 'دورات الشحن', s2, b.cycleCount + ' دورة', s2 === 'ok' ? '' : 'البطارية قاربت نهاية عمرها.');
    } else setR(P.k + 'cycles', P.n + 'دورات الشحن', 'info', 'هذا الجهاز/الأندرويد لا يكشف عدد الدورات (يتوفر من أندرويد 14).');
  }


  /* ───────── تقييم البطارية العميق (قراءات حقيقية) ───────── */
  function num(v) { var n = parseFloat(v); return isFinite(n) ? n : null; }
  var OCV = [[0, 3400], [10, 3600], [20, 3700], [40, 3770], [60, 3850], [80, 4000], [100, 4200]];
  function ocvAt(l) {
    for (var i = 1; i < OCV.length; i++) if (l <= OCV[i][0]) { var a = OCV[i - 1], b = OCV[i]; return a[1] + (b[1] - a[1]) * (l - a[0]) / (b[0] - a[0]); }
    return 4200;
  }
  function healthFrom(bd) {
    var sf = bd.sysfs || {}, b = bd.battery || {}, cf = num(sf.charge_full), cfd = num(sf.charge_full_design);
    if (cf && cfd && cfd > 0) return { pct: cf * 100 / cfd, src: 'قراءة مباشرة من البطارية (السعة الحالية ÷ سعة المصنع)', real: true, cur: cf / 1000, des: cfd / 1000 };
    var ef = num(sf.energy_full), efd = num(sf.energy_full_design);
    if (ef && efd && efd > 0) return { pct: ef * 100 / efd, src: 'قراءة مباشرة من البطارية (الطاقة الحالية ÷ طاقة المصنع)', real: true };
    var fc = num(sf.fg_fullcaprep), fn = num(sf.fg_fullcapnom);
    if (fc && fn && fn > 0 && fc / fn < 1.6) return { pct: fc * 100 / fn, src: 'قراءة من دائرة قياس البطارية (fuel gauge)', real: true };
    if (bd.soh > 0 && bd.soh <= 100) return { pct: bd.soh, src: 'حالة الصحة من النظام (أندرويد 14+)', real: true };
    var cc = b.chargeCounter || 0, lvl = b.level, des = bd.designMah;
    if (cc > 0 && lvl >= 20 && lvl <= 95 && des > 500) {
      var est = cc / 1000 * 100 / lvl;
      if (est < des * 0.2 || est > des * 1.6) est = cc * 100 / lvl;
      if (est >= des * 0.2 && est <= des * 1.6) return { pct: Math.min(100, est * 100 / des), src: 'تقدير من عدّاد الشحن (تقريبي، أدق إذا النسبة بين 30–90%)', real: false, cur: est, des: des };
    }
    return null;
  }
  function evalBatteryDeep(bd, P) {
    if (!bd) return;
    var b = bd.battery || {}, sf = bd.sysfs || {}, h = healthFrom(bd);
    if (h) {
      var st = h.pct >= 80 ? 'ok' : h.pct >= 60 ? 'warn' : 'bad', word = h.pct >= 90 ? 'ممتازة' : h.pct >= 80 ? 'جيدة' : h.pct >= 70 ? 'متوسطة (بدأت تضعف)' : h.pct >= 60 ? 'ضعيفة' : 'متهالكة';
      setR(P.k + 'bh', P.n + 'صحة البطارية (العمر)', st, 'الصحة ≈ ' + Math.round(h.pct) + '% — ' + word + (h.cur ? ' | السعة الفعلية ≈ ' + Math.round(h.cur) + ' من ' + Math.round(h.des) + ' mAh' : '') + ' | المصدر: ' + h.src,
        st === 'ok' ? '' : (st === 'bad' ? 'السعة نزلت كثير؛ استبدال البطارية هو الحل.' : 'تتحمل وقت أقل من أول؛ إذا تنطفي بسرعة غيّرها.') + (h.real ? '' : ' (الرقم تقريبي: أعد القياس والنسبة بين 30 و90% بدون تشغيل ثقيل)'));
    } else {
      setR(P.k + 'bh', P.n + 'صحة البطارية (العمر)', 'info', 'هذا الجهاز لا يكشف سعة المصنع/السعة الحالية للتطبيقات.', 'استعمل اختبار الحمل (يقيس مقاومة البطارية) كمؤشر للضعف، أو *#0228# / قوائم الصيانة بالجهاز إن وجدت.');
    }
    var cyc = b.cycleCount > 0 ? b.cycleCount : (num(sf.cycle_count) || num(sf.battery_cycle) || 0);
    if (cyc > 0) setR(P.k + 'cycles', P.n + 'دورات الشحن', cyc > 800 ? 'bad' : cyc > 500 ? 'warn' : 'ok', cyc + ' دورة (البطارية تتحمل عادة 500–800)', cyc > 500 ? 'قاربت نهاية عمرها.' : '');
    if (b.plugged === 0 && b.level >= 15 && b.voltage > 0) {
      var exp = ocvAt(b.level), d = exp - b.voltage;
      var s2 = d > 250 ? 'bad' : d > 160 ? 'warn' : 'ok';
      setR(P.k + 'bcurve', P.n + 'الفولتية مقابل النسبة', s2, 'عند ' + b.level + '% المتوقع ≈ ' + Math.round(exp) + ' mV والمقروء ' + b.voltage + ' mV (الفرق ' + Math.round(d) + ')',
        s2 === 'ok' ? '' : 'الفولتية أقل من الطبيعي لهذه النسبة: علامة بطارية ضعيفة أو نسبة غير صادقة (أعد القياس بعد ما تهدأ الشاشة والمعالج).');
    }
    if (bd.maxChargeCurrent > 0) setR(P.k + 'bmaxchg', P.n + 'أقصى تيار شحن معلن', 'info', Math.round(bd.maxChargeCurrent / 1000) + ' mA' + (bd.maxChargeVoltage > 0 ? ' @ ' + (bd.maxChargeVoltage / 1e6).toFixed(1) + 'V' : ''));
    batterySummary(P);
  }

  function nI(arr) { var m = 0; arr.forEach(function (x) { m = Math.max(m, Math.abs(x)); }); return m > 20000 ? 1000 : 1; }
  function avg(a) { return a.length ? a.reduce(function (x, y) { return x + y; }, 0) / a.length : 0; }
  function analyzeLoad(r) {
    var S = r.samples || [], idle = S.filter(function (x) { return x.phase === 'idle'; }), load = S.filter(function (x) { return x.phase === 'load'; });
    var all = S.map(function (x) { return x.i; }).filter(function (x) { return typeof x === 'number'; }), sc = nI(all);
    var o = { unplugged: S.every(function (x) { return !x.plugged; }), ir: null };
    var steady = load.filter(function (x) { return x.t >= (idle.length ? idle[idle.length - 1].t : 0) + 3000; });
    if (!steady.length) steady = load;
    o.idleV = avg(idle.map(function (x) { return x.v; })); o.loadV = avg(steady.map(function (x) { return x.v; }));
    o.idleI = avg(idle.filter(function (x) { return typeof x.i === 'number'; }).map(function (x) { return Math.abs(x.i) / sc; }));
    o.loadI = avg(steady.filter(function (x) { return typeof x.i === 'number'; }).map(function (x) { return Math.abs(x.i) / sc; }));
    o.sag = o.idleV - o.loadV;
    if (o.unplugged && o.loadI - o.idleI >= 250 && o.sag > 0) o.ir = o.sag / (o.loadI - o.idleI);
    var temps = S.map(function (x) { return x.temp; }).filter(function (x) { return typeof x === 'number'; });
    o.tempRise = temps.length ? (Math.max.apply(null, temps) - temps[0]) / 10 : 0; o.tempMax = temps.length ? Math.max.apply(null, temps) / 10 : 0;
    var bk = (r.buckets || []).slice(1), th = Math.max(1, Math.floor(bk.length / 3));
    var first = avg(bk.slice(0, th)), last = avg(bk.slice(bk.length - th));
    o.throttle = first > 0 ? last / first : 1; o.cores = r.cores;
    return o;
  }
  function evalLoad(r, P) {
    var o = analyzeLoad(r);
    if (!o.unplugged) setR(P.k + 'bir', P.n + 'مقاومة البطارية (تحت الحمل)', 'info', 'الشاحن كان موصول أثناء الاختبار فما يصير قياس صحيح.', 'افصل الشاحن وأعد الاختبار.');
    else if (o.ir === null) setR(P.k + 'bir', P.n + 'مقاومة البطارية (تحت الحمل)', 'info', 'الجهاز ما يعطي قراءة تيار كافية لحساب المقاومة. هبوط الفولتية تحت الحمل: ' + Math.round(o.sag) + ' mV', '');
    else {
      var st = o.ir > 0.35 || o.sag > 400 ? 'bad' : o.ir > 0.2 || o.sag > 300 ? 'warn' : 'ok', w = st === 'ok' ? 'سليمة' : st === 'warn' ? 'بدأت تضعف' : 'ضعيفة/متهالكة';
      setR(P.k + 'bir', P.n + 'مقاومة البطارية (تحت الحمل)', st, 'المقاومة ≈ ' + (o.ir * 1000).toFixed(0) + ' mΩ — ' + w + ' | الفولتية نزلت ' + Math.round(o.sag) + ' mV عند تيار ≈ ' + Math.round(o.loadI) + ' mA',
        st === 'ok' ? '' : 'البطارية ما تتحمل الأحمال: الجهاز ينطفي فجأة أو تنزل النسبة بسرعة؛ الأفضل استبدالها. (القيم الطبيعية للبطارية الجيدة أقل من 200 mΩ)');
    }
    var st2 = o.tempMax >= 45 || o.throttle < 0.5 ? 'bad' : o.tempRise >= 6 || o.throttle < 0.75 ? 'warn' : 'ok';
    setR(P.k + 'cputh', P.n + 'أداء المعالج والحرارة تحت الحمل', st2,
      'الأنوية ' + (o.cores || '?') + ' | تراجع الأداء ' + Math.max(0, Math.round((1 - o.throttle) * 100)) + '% | ارتفاع حرارة البطارية ' + o.tempRise.toFixed(1) + '°C (الحد ' + o.tempMax.toFixed(1) + ')',
      st2 === 'ok' ? '' : 'الجهاز يسخن أو يخفّض الأداء بسرعة: نظّف الغبار/الغطاء، وإذا استمر مع بطارية سليمة فالتبريد أو اللوحة فيها مشكلة.');
    batterySummary(P);
    return o;
  }

  function evalCharge(stats, P) {
    var st = 'ok', fix = '', notes = [];
    notes.push('النوع: ' + stats.type + (stats.maxCur ? ' (الشاحن معلن ' + Math.round(stats.maxCur) + ' mA)' : ''));
    notes.push('الحالة: ' + (stats.charging ? 'يشحن' : 'لا يشحن'));
    if (stats.cur) notes.push('التيار الفعلي ≈ ' + Math.round(stats.cur) + ' mA (' + (stats.cur * stats.v / 1e6).toFixed(1) + ' واط)');
    if (stats.ccDelta !== null) notes.push('تغيّر عدّاد الشحن ' + (stats.ccDelta >= 0 ? '+' : '') + Math.round(stats.ccDelta / 1000) + ' mAh خلال ' + stats.secs + ' ث');
    if (!stats.charging) { st = 'bad'; fix = 'يشوف الشاحن بس ما يشحن؛ جرّب كيبل/شاحن ثاني ونظّف منفذ الشحن.'; }
    else if (stats.cur && stats.cur < 300 && stats.level < 90) { st = 'warn'; fix = 'الشحن بطيء جدًا: كيبل/شاحن ضعيف أو منفذ متّسخ أو دائرة شحن متعبة.'; }
    else if (stats.ccDelta !== null && stats.ccDelta <= 0 && stats.level < 95) { st = 'warn'; fix = 'يقول يشحن لكن الشحنة ما تزيد فعليًا: الجهاز يستهلك بقدر الشحن أو الشاحن ضعيف.'; }
    if (stats.maxCur && stats.cur && stats.cur < stats.maxCur * 0.3 && stats.level < 80 && st === 'ok') { st = 'warn'; fix = 'التيار أقل بكثير من قدرة الشاحن: الكيبل أو منفذ الجهاز يحدّ الشحن.'; }
    if (stats.temp >= 45) { st = 'bad'; fix = 'حرارة عالية أثناء الشحن؛ افصل الشاحن.'; }
    setR(P.k + 'bchg', P.n + 'قياس الشحن', st, notes.join(' | '), fix);
    batterySummary(P);
  }

  function batterySummary(P) {
    var keys = ['bh', 'cycles', 'bir', 'bcurve', 'bchg', 'battery'], worst = 'ok', bad = [], warn = [], have = 0;
    var rank = { ok: 0, info: 0, warn: 1, bad: 2 };
    keys.forEach(function (k) {
      var r = R.get(P.k + k); if (!r) return; have++;
      if (rank[r.status] > rank[worst]) worst = r.status;
      if (r.status === 'bad') bad.push(r.name); else if (r.status === 'warn') warn.push(r.name);
    });
    if (!have) return;
    var verdict = worst === 'bad' ? 'البطارية فيها مشكلة (ضعيفة/تالفة)' : worst === 'warn' ? 'البطارية متوسطة/بدأت تضعف' : 'البطارية سليمة بحسب الفحوصات المتوفرة';
    setR(P.k + 'bsum', P.n + 'الخلاصة: البطارية', worst, verdict + (bad.length || warn.length ? ' — الأسباب: ' + bad.concat(warn).join('، ') : '') + ' (بُنيت على ' + have + ' فحص)',
      worst === 'ok' ? 'لحكم أدق: شغّل «صحة البطارية» و«اختبار الحمل» و«قياس الشحن» كلهم.' : 'راجع تفاصيل كل فحص بالأسفل.');
  }

  function evalSystem(s, P) {
    setR(P.k + 'device', P.n + 'معلومات الجهاز', 'info', s.manufacturer + ' ' + s.model + ' | أندرويد ' + s.release + ' | يعمل منذ ' + (s.uptimeMs / 86400000).toFixed(1) + ' يوم');
    if (s.cores) setR(P.k + 'hw', P.n + 'الهاردوير', 'info', 'معالج ' + (s.hardware || '?') + ' | ' + s.cores + ' أنوية | ' + (s.abi || '') + ' | لوحة ' + (s.board || '?') + (s.dispW ? ' | شاشة ' + s.dispW + '×' + s.dispH + ' @' + s.dpi + 'dpi' : ''));
    if (s.sdk) setR(P.k + 'sw', P.n + 'السوفتوير', s.sdk < 26 ? 'warn' : 'ok', 'أندرويد ' + s.release + ' (API ' + s.sdk + ')' + (s.uptimeMs > 30 * 86400000 ? ' | ما انعمل له إعادة تشغيل من أكثر من 30 يوم' : ''), s.sdk < 26 ? 'النظام قديم: تطبيقات كثيرة ما تشتغل عليه وبدون ترقيع أمني.' : (s.uptimeMs > 30 * 86400000 ? 'أعد تشغيل الجهاز.' : ''));
    var usedRam = 100 - s.ramAvail * 100 / s.ramTotal;
    setR(P.k + 'ram', P.n + 'الذاكرة RAM', usedRam > 90 ? 'warn' : 'ok', 'الإجمالي ' + fmt(s.ramTotal) + ' | مستخدم ' + usedRam.toFixed(0) + '%', usedRam > 90 ? 'أغلق التطبيقات الثقيلة.' : '');
    if (s.diskTotal) {
      var freePct = s.diskFree * 100 / s.diskTotal;
      var ds = freePct < 5 ? 'bad' : freePct < 15 ? 'warn' : 'ok';
      setR(P.k + 'disk', P.n + 'التخزين', ds, 'الإجمالي ' + fmt(s.diskTotal) + ' | الفارغ ' + fmt(s.diskFree) + ' (' + freePct.toFixed(0) + '%)', ds === 'ok' ? '' : 'امسح ملفات أو تطبيقات غير مهمة.');
    }
    if (s.thermal >= 0) {
      var ts = s.thermal >= 3 ? 'bad' : s.thermal === 2 ? 'warn' : 'ok';
      setR(P.k + 'thermal', P.n + 'الحرارة العامة', ts, 'المستوى ' + s.thermal + ' (0 طبيعي … 6 طوارئ)', ts === 'ok' ? '' : 'الجهاز يسخن: أغلق التطبيقات وأخرج الغطاء.');
    }
    if (s.patch) {
      var age = (Date.now() - new Date(s.patch).getTime()) / 86400000;
      if (isFinite(age)) setR(P.k + 'patch', P.n + 'تحديث الأمان', age > 365 ? 'warn' : 'ok', 'آخر باتش ' + s.patch, age > 365 ? 'النظام قديم أمنيًا؛ حدّثه إن توفر.' : '');
    }
    if (s.rooted) setR(P.k + 'root', P.n + 'صلاحيات Root', 'info', 'الجهاز يبدو مُجذَّرًا (Root).');
  }

  function evalSensors(list, P) {
    var types = {};
    (list || []).forEach(function (x) { types[x.type] = true; });
    var missing = Object.keys(STYPES).filter(function (t) { return !types[t]; });
    if (!missing.length) setR(P.k + 'sensorlist', P.n + 'الحساسات الأساسية', 'ok', 'كلها موجودة (' + (list || []).length + ' حساس إجمالًا)');
    missing.forEach(function (t) {
      var m = STYPES[t];
      setR(P.k + 'sens_' + m[0], P.n + 'حساس مفقود: ' + m[1], m[2], 'غير ظاهر للنظام', 'إذا الموديل يفترض فيه هذا الحساس فهو معطّل؛ بعض الموبايلات الاقتصادية ما فيها.');
    });
  }

  async function networkCheck() {
    var online = navigator.onLine, ok = false;
    if (online) {
      try {
        var ctl = new AbortController(), t = setTimeout(function () { ctl.abort(); }, 5000);
        await fetch('https://www.gstatic.com/generate_204', { mode: 'no-cors', cache: 'no-store', signal: ctl.signal });
        clearTimeout(t); ok = true;
      } catch (e) { ok = false; }
    }
    var c = navigator.connection;
    setR('net', 'الإنترنت', ok ? 'ok' : 'warn', ok ? 'الاتصال يعمل' + (c && c.effectiveType ? ' (' + c.effectiveType + ')' : '') : 'لا يوجد اتصال فعلي بالإنترنت', ok ? '' : 'افحص الواي فاي/البيانات والشريحة.');
  }

  async function mediaCount() {
    try {
      var dv = await navigator.mediaDevices.enumerateDevices();
      var cams = dv.filter(function (d) { return d.kind === 'videoinput'; }).length;
      var mics = dv.filter(function (d) { return d.kind === 'audioinput'; }).length;
      setR('media', 'الكاميرا والمايك', 'info', 'اكتُشف ' + cams + ' كاميرا و' + mics + ' مايك — اعمل الاختبار اليدوي للتأكد.');
    } catch (e) { /* ignore */ }
  }

  /* ───────── الاختبارات اليدوية ───────── */
  var ov = $('#overlay'), cleanup = null;
  var seq = null;
  function open(html) {
    ov.innerHTML = html; ov.hidden = false;
    if (seq) {
      ov.insertAdjacentHTML('beforeend', '<div class="obar"><span>الفحص الشامل: اختبار ' + seq.i + ' من ' + seq.list.length + '</span><button id="skip">تخطي ⏭</button><button id="stopSeq" class="red">إيقاف</button></div>');
      $('#skip').onclick = function () { close(); };
      $('#stopSeq').onclick = function () { seq = null; close(); };
    }
    return ov;
  }
  function close() {
    if (cleanup) { try { cleanup(); } catch (e) {} cleanup = null; }
    ov.hidden = true; ov.innerHTML = '';
    if (seq) setTimeout(seqNext, 60); else goReport();
  }
  function seqNext() {
    if (!seq) return;
    if (seq.i >= seq.list.length) { seq = null; goReport(); return; }
    var k = seq.list[seq.i++];
    try { var r = TESTS[k](); if (r && r.catch) r.catch(function () { close(); }); } catch (e) { close(); }
  }
  function autoScreen(title, msg) {
    open('<div class="center"><div><b>' + title + '</b></div><div id="amsg">' + msg + '</div><div class="meter"><i id="lvl"></i></div></div><div class="obar"><button id="cancel" class="red">إلغاء</button></div>');
    $('#cancel').onclick = close;
  }
  function bar(p) { var l = $('#lvl'); if (l) l.style.width = p + '%'; }
  function amsg(t) { var m = $('#amsg'); if (m) m.innerHTML = t; }
  var sleep = function (ms) { return new Promise(function (r) { setTimeout(r, ms); }); };
  function alive() { return !ov.hidden; }
  function verdict(key, name, okTxt, badTxt, fix) {
    var o = ov;
    o.querySelector('#vOk').onclick = function () { close(); setR(key, name, 'ok', okTxt); };
    o.querySelector('#vBad').onclick = function () { close(); setR(key, name, 'bad', badTxt, fix); };
  }
  var BAR = '<div class="obar"><span id="hint"></span><button id="vOk">✔ سليم</button><button id="vBad" class="red">✖ فيه مشكلة</button></div>';

  var TESTS = {
    touch: function () {
      open('<div id="tgrid"></div><div class="obar"><span id="tinfo">مرّر إصبعك على كل الشاشة حتى تصير كل المربعات خضراء</span><button id="tdone">إنهاء</button></div>');
      var g = $('#tgrid'), cols = 6, rows = 10, cells = [], maxT = 0;
      g.style.gridTemplateColumns = 'repeat(' + cols + ',1fr)';
      for (var i = 0; i < cols * rows; i++) { var d = document.createElement('div'); d.className = 'cell'; g.appendChild(d); cells.push(d); }
      function left() { return cells.filter(function (c) { return !c.classList.contains('hit'); }).length; }
      function mark(e) {
        e.preventDefault(); maxT = Math.max(maxT, e.touches.length);
        for (var k = 0; k < e.touches.length; k++) {
          var el = document.elementFromPoint(e.touches[k].clientX, e.touches[k].clientY);
          if (el && el.classList.contains('cell')) el.classList.add('hit');
        }
        $('#tinfo').textContent = 'المتبقي: ' + left() + ' | أقصى لمسات متزامنة: ' + maxT;
      }
      g.addEventListener('touchstart', mark, { passive: false });
      g.addEventListener('touchmove', mark, { passive: false });
      $('#tdone').onclick = function () {
        var l = left(); close();
        if (l === 0) setR('t_touch', 'اختبار اللمس', 'ok', 'كل المناطق تستجيب. أقصى لمسات متزامنة: ' + maxT);
        else setR('t_touch', 'اختبار اللمس', l <= 3 ? 'warn' : 'bad', l + ' منطقة من ' + (cols * rows) + ' ما استجابت', 'نظّف الشاشة وأعد الاختبار؛ إذا بقيت مناطق ميتة فالمَلمس أو الشاشة تالفة.');
      };
    },
    screen: function () {
      var colors = ['#f00', '#0f0', '#00f', '#fff', '#000'], i = 0;
      open('<div class="full" id="sc"></div>' + BAR);
      var sc = $('#sc'); sc.style.background = colors[0];
      $('#hint').textContent = 'المس الشاشة لتغيير اللون وابحث عن بكسلات ميتة أو بقع أو خطوط';
      sc.onclick = function () { i = (i + 1) % colors.length; sc.style.background = colors[i]; };
      verdict('t_screen', 'اختبار الشاشة', 'لا بكسلات ميتة أو خطوط', 'لوحظ عيب في الشاشة (بكسل/خط/بقعة)', 'عيوب العرض غالبًا من لوحة الشاشة نفسها؛ تحتاج استبدال الشاشة.');
    },
    speaker: function () {
      open('<div class="center"><div>اضغط وتأكد أنك تسمع الصوت بوضوح ومن غير تقطيع<br><small>قد تكون السماعة أحادية فلا يظهر فرق يمين/يسار</small></div>' +
        '<div class="obar"><button id="bL">يسار</button><button id="bC">وسط</button><button id="bR">يمين</button></div></div>' + BAR);
      var ctx = new (window.AudioContext || window.webkitAudioContext)();
      function beep(p) {
        var o = ctx.createOscillator(), pn = ctx.createStereoPanner ? ctx.createStereoPanner() : null;
        if (pn) { pn.pan.value = p; o.connect(pn); pn.connect(ctx.destination); } else o.connect(ctx.destination);
        o.frequency.value = 440; o.start(); setTimeout(function () { o.stop(); }, 700);
      }
      $('#bL').onclick = function () { ctx.resume(); beep(-1); };
      $('#bC').onclick = function () { ctx.resume(); beep(0); };
      $('#bR').onclick = function () { ctx.resume(); beep(1); };
      cleanup = function () { ctx.close(); };
      verdict('t_speaker', 'السماعة', 'الصوت واضح', 'ما سمعت صوت أو فيه تقطيع/تشويش', 'نظّف شبكة السماعة؛ إذا استمر فالسماعة تالفة.');
    },
    mic: async function () {
      open('<div class="center"><div>اضغط «ابدأ» ثم تكلم بصوت عالٍ لمدة 4 ثواني</div><div class="meter"><i id="lvl"></i></div><button class="big" id="go" style="max-width:260px">ابدأ</button></div><div class="obar"><button id="cancel" class="red">إلغاء</button></div>');
      $('#cancel').onclick = close;
      $('#go').onclick = async function () {
        try {
          var stream = await navigator.mediaDevices.getUserMedia({ audio: true });
          var ac = new (window.AudioContext || window.webkitAudioContext)(), an = ac.createAnalyser(); an.fftSize = 512;
          ac.createMediaStreamSource(stream).connect(an);
          var buf = new Uint8Array(an.fftSize), peak = 0, stop = false;
          cleanup = function () { stop = true; stream.getTracks().forEach(function (t) { t.stop(); }); ac.close(); };
          var t0 = Date.now();
          (function loop() {
            if (stop) return;
            an.getByteTimeDomainData(buf);
            var m = 0; for (var i = 0; i < buf.length; i++) m = Math.max(m, Math.abs(buf[i] - 128));
            peak = Math.max(peak, m); var l = $('#lvl'); if (l) l.style.width = Math.min(100, m * 2.5) + '%';
            if (Date.now() - t0 > 4000) {
              close();
              if (peak > 12) setR('t_mic', 'المايك', 'ok', 'التقط الصوت (ذروة ' + peak + ')');
              else setR('t_mic', 'المايك', 'bad', 'ما التقط صوتًا', 'تأكد من إذن المايك ونظّف فتحة المايك؛ إذا استمر فالمايك تالف.');
              return;
            }
            requestAnimationFrame(loop);
          })();
        } catch (e) { close(); setR('t_mic', 'المايك', 'bad', 'تعذّر فتح المايك: ' + (e.name || e), 'امنح إذن المايك للتطبيق؛ NotReadableError تعني المايك مشغول أو معطل.'); }
      };
    },
    vibrate: function () {
      var sup = !!navigator.vibrate;
      open('<div class="center"><div>' + (sup ? 'اضغط للاهتزاز وانتبه هل تحس به' : 'هذا الجهاز لا يدعم الاهتزاز من التطبيق') + '</div><button class="big" id="vb" style="max-width:260px">📳 اهتزاز</button></div>' + BAR);
      $('#vb').onclick = function () { navigator.vibrate && navigator.vibrate([400, 150, 400]); };
      verdict('t_vib', 'الاهتزاز', 'المحرك يهتز', 'ما اهتز', 'تأكد أن وضع الاهتزاز غير مقفل؛ وإلا فمحرك الاهتزاز تالف.');
    },
    sensors: async function () {
      open('<div class="center" id="sres">... جاري قراءة الحساسات، حرّك الجهاز وغطِّ حساس القرب بإصبعك</div><div class="obar"><button id="cancel" class="red">إغلاق</button></div>');
      $('#cancel').onclick = close;
      if (!DD) { $('#sres').textContent = 'يحتاج نسخة APK الأصلية'; return; }
      var list = [[1, 'accel', 'مقياس التسارع'], [4, 'gyro', 'الجيروسكوب'], [2, 'mag', 'البوصلة'], [5, 'light', 'الإضاءة'], [8, 'prox', 'القرب']], lines = [];
      for (var i = 0; i < list.length; i++) {
        var t = list[i], r;
        try { r = await DD.readSensor({ type: t[0] }); } catch (e) { r = { available: false }; }
        if (!r.available) { setR('t_' + t[1], 'حساس ' + t[2], t[0] === 1 ? 'bad' : 'warn', 'غير موجود', 'إذا الموديل يفترض فيه هذا الحساس فهو معطّل.'); lines.push(t[2] + ': غير موجود'); }
        else if (!r.samples) { setR('t_' + t[1], 'حساس ' + t[2], 'bad', 'موجود لكن ما أعطى قراءات', 'الحساس معطّل أو متوقف.'); lines.push(t[2] + ': بلا قراءات'); }
        else {
          var v = (r.values || []).map(function (x) { return x.toFixed(2); }).join(', '), st = 'ok', fix = '';
          if (t[0] === 1 && r.values.length >= 3) {
            var g = Math.sqrt(r.values[0] * r.values[0] + r.values[1] * r.values[1] + r.values[2] * r.values[2]);
            if (g < 7 || g > 12) { st = 'warn'; fix = 'قراءة التسارع غير منطقية (المفروض ≈9.8)؛ ضع الجهاز ثابتًا وأعد الاختبار.'; }
            v += ' (g≈' + g.toFixed(1) + ')';
          }
          if (t[0] === 4 && r.values.length >= 3) { var gm = Math.max(Math.abs(r.values[0]), Math.abs(r.values[1]), Math.abs(r.values[2])); if (gm > 0.15) { st = 'warn'; fix = 'الجيروسكوب يعطي حركة والجهاز ثابت؛ ثبّت الجهاز على سطح وأعد القياس، وإذا استمر فهو يحتاج معايرة/تالف.'; } }
          if (t[0] === 2 && r.values.length >= 3) { var mg = Math.sqrt(r.values[0] * r.values[0] + r.values[1] * r.values[1] + r.values[2] * r.values[2]); v += ' (شدة≈' + mg.toFixed(0) + 'µT)'; if (mg < 15 || mg > 90) { st = 'warn'; fix = 'شدة المجال خارج المعتاد (25–65): ابعد عن المغناطيس/المعادن وأعد القياس، أو البوصلة محتاجة معايرة (حرّك الجهاز بشكل 8).'; } }
          setR('t_' + t[1], 'حساس ' + t[2], st, 'قراءة: ' + v, fix); lines.push(t[2] + ': ' + v);
        }
        $('#sres') && ($('#sres').innerHTML = lines.map(esc).join('<br>'));
      }
    }
  };


  /* ───────── اختبارات إضافية: سماعة الأذن، الإضاءة، البطارية، الشحن، الرام، التخزين ───────── */
  TESTS.earpiece = function () {
    open('<div class="center"><div>ضع أذنك على سماعة المكالمات (العلوية) واضغط الزر؛ لازم تسمع نغمة من الأعلى فقط<br><small>إذا طلع الصوت من الأسفل أو ما طلع شي فالسماعة فيها مشكلة</small></div><button class="big" id="ep" style="max-width:260px">🔈 شغّل سماعة الأذن</button></div>' + BAR);
    $('#ep').onclick = async function () {
      if (!DD || !DD.playTone) { $('#hint').textContent = 'يحتاج نسخة APK الأصلية'; return; }
      try { await DD.playTone({ route: 'earpiece' }); } catch (e) { $('#hint').textContent = 'تعذّر التشغيل: ' + (e.message || e); }
    };
    verdict('t_ear', 'سماعة الأذن (الداخلية)', 'الصوت واضح من السماعة العلوية', 'ما اشتغلت أو الصوت مشوّش', 'نظّف شبكة السماعة العلوية من الغبار؛ إذا استمر فالسماعة تالفة أو فيها كيبل مفصول.');
  };
  TESTS.loud = function () {
    open('<div class="center"><div>اضغط لتشغيل نغمة من السبيكر الخارجي (السفلي) بأعلى صوت، واسمع هل فيه تقطيع أو خشخشة</div><button class="big" id="ls" style="max-width:260px">🔊 شغّل السبيكر الخارجي</button></div>' + BAR);
    $('#ls').onclick = async function () {
      if (!DD || !DD.playTone) { $('#hint').textContent = 'يحتاج نسخة APK الأصلية'; return; }
      try { await DD.playTone({ route: 'speaker' }); } catch (e) { $('#hint').textContent = 'تعذّر التشغيل: ' + (e.message || e); }
    };
    verdict('t_loud', 'السبيكر الخارجي', 'صوت قوي وواضح', 'صوت ضعيف/مقطّع/ما يشتغل', 'نظّف الشبكة؛ ارفع الصوت للأقصى؛ إذا استمر فالسبيكر تالف أو دخله ماء.');
  };
  TESTS.backlight = function () {
    var steps = [['#fff', 'أبيض: دوّر على بقع صفراء أو داكنة أو إضاءة غير متساوية'], ['#000', 'أسود (في غرفة معتمة): دوّر على تسريب ضوء من الحواف أو بقع مضيئة'], ['#808080', 'رمادي: دوّر على خطوط أو تموّج أو وميض'], ['#fff', 'قلل السطوع وزوّده من إعدادات الجهاز ولاحظ هل الإضاءة تتغير بسلاسة بدون وميض']], i = 0;
    open('<div class="full" id="sc"></div>' + BAR);
    var sc = $('#sc'); sc.style.background = steps[0][0];
    $('#hint').textContent = steps[0][1] + ' (المس الشاشة للتالي)';
    sc.onclick = function () { i = (i + 1) % steps.length; sc.style.background = steps[i][0]; $('#hint').textContent = steps[i][1] + ' (المس للتالي)'; };
    verdict('t_backlight', 'إضاءة الشاشة (Backlight)', 'الإضاءة متساوية بدون وميض أو بقع', 'فيه بقع/تسريب ضوء/وميض/خفوت', 'مشاكل الإضاءة تكون من لوحة الشاشة أو كيبل الإضاءة؛ تحتاج صيانة أو استبدال شاشة.');
  };
  TESTS.flash = async function () {
    open('<div class="center"><div id="fm">جاري فتح الكاميرا الخلفية لتشغيل الفلاش...</div></div>' + BAR);
    var stream = null;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: 'environment' } } });
      cleanup = function () { stream.getTracks().forEach(function (t) { t.stop(); }); };
      var tr = stream.getVideoTracks()[0], cap = tr.getCapabilities ? tr.getCapabilities() : {};
      if (!cap.torch) { close(); return setR('t_flash', 'فلاش الكاميرا', 'info', 'هذا الجهاز/المتصفح لا يسمح بالتحكم بالفلاش من التطبيق. جرّبه من تطبيق الكاميرا أو الكشاف.'); }
      await tr.applyConstraints({ advanced: [{ torch: true }] });
      $('#fm').textContent = 'الفلاش يجب أن يكون مضاءً الآن';
      verdict('t_flash', 'فلاش الكاميرا', 'اشتغل', 'ما اشتغل', 'جرّب الكشاف من القائمة؛ إذا ما اشتغل فاللمبة أو دائرتها تالفة.');
    } catch (e) { close(); setR('t_flash', 'فلاش الكاميرا', 'warn', 'تعذّر تشغيل الفلاش: ' + (e.name || e), 'امنح إذن الكاميرا أو جرّب الكشاف.'); }
  };
  TESTS.batteryTest = async function () {
    autoScreen('قياس البطارية', 'جاري أخذ قراءات لمدة 10 ثواني...');
    if (!DD) { amsg('يحتاج نسخة APK الأصلية'); return; }
    var vs = [], cs = [], t = [], last = null;
    for (var i = 0; i < 10 && alive(); i++) {
      try { last = await DD.getBattery(); vs.push(last.voltage); t.push(last.temperature); if (typeof last.currentNow === 'number') cs.push(last.currentNow); } catch (e) {}
      bar((i + 1) * 10); await sleep(1000);
    }
    if (!alive()) return;
    close();
    if (!last || !last.available) return setR('t_battery', 'قياس البطارية', 'bad', 'ما انقرأت أي قراءة من البطارية', 'البطارية غير موصولة أو دائرة الشحن فيها عطل.');
    var vmin = Math.min.apply(null, vs), vmax = Math.max.apply(null, vs), st = 'ok', fix = '', notes = [];
    if (vmax < 3000 || vmax > 4600) { st = 'bad'; fix = 'فولتية غير منطقية؛ البطارية أو دائرة القياس تالفة.'; }
    else if (vmax - vmin > 150) { st = 'warn'; fix = 'الفولتية تتذبذب بشدة؛ علامة بطارية ضعيفة أو تلامس سيئ.'; }
    if (last.level > 30 && vmax < 3500) { st = 'bad'; fix = 'النسبة تقول ' + last.level + '% بس الفولتية واطئة؛ البطارية منتفخة/ضعيفة والنسبة غير صادقة.'; }
    if (Math.max.apply(null, t) >= 45) { st = 'bad'; fix = 'حرارة عالية بدون حمل؛ اطفِ الجهاز وافحص البطارية.'; }
    notes.push('الفولتية ' + vmin + '–' + vmax + ' mV', 'الحرارة ' + last.temperature.toFixed(1) + '°C', 'النسبة ' + last.level + '%');
    if (cs.length) notes.push('التيار ≈ ' + Math.round(cs.reduce(function (a, b) { return a + Math.abs(b); }, 0) / cs.length / (Math.abs(cs[0]) > 20000 ? 1000 : 1)) + ' mA');
    setR('t_battery', 'قياس البطارية', st, notes.join(' | '), fix);
    evalBattery(last, LOCAL);
  };
  TESTS.chargeTest = async function () {
    autoScreen('قياس الشحن', 'اسحب الشاحن إذا موصول...');
    if (!DD) { amsg('يحتاج نسخة APK الأصلية'); return; }
    var PL = { 1: 'شاحن جداري AC', 2: 'USB', 4: 'لاسلكي' }, phase = 0, t0 = Date.now(), base = null, S = [];
    while (alive() && Date.now() - t0 < 60000) {
      var b; try { b = await DD.getBattery(); } catch (e) { b = null; }
      if (b) {
        if (phase === 0) { if (!b.plugged) { phase = 1; t0 = Date.now(); amsg('الحين وصّل الشاحن بالجهاز (60 ثانية)'); } else amsg('اسحب الشاحن أولًا'); }
        else if (phase === 1 && b.plugged) { phase = 2; base = Date.now(); S = []; amsg('تم اكتشاف الشاحن (' + (PL[b.plugged] || b.plugged) + ')... جاري القياس 20 ثانية، لا تلمس الجهاز'); }
        else if (phase === 2) {
          S.push(b); bar(Math.min(100, (Date.now() - base) / 200));
          if (Date.now() - base > 20000) {
            close();
            var cs = S.map(function (x) { return x.currentNow; }).filter(function (x) { return typeof x === 'number'; }), sc = nI(cs), last = S[S.length - 1], first = S[0];
            var cur = avg(cs.map(function (x) { return Math.abs(x) / sc; }));
            var ccd = (typeof first.chargeCounter === 'number' && typeof last.chargeCounter === 'number' && first.chargeCounter > 0) ? (last.chargeCounter - first.chargeCounter) * (Math.abs(first.chargeCounter) < 20000 ? 1000 : 1) : null;
            var bd = null; try { bd = await DD.batteryDeep(); } catch (e) {}
            return evalCharge({ type: PL[last.plugged] || last.plugged, charging: S.filter(function (x) { return x.status === 2 || x.status === 5; }).length > S.length / 2, cur: cur, v: last.voltage,
              ccDelta: ccd, secs: Math.round((Date.now() - base) / 1000), level: last.level, temp: last.temperature, maxCur: bd && bd.maxChargeCurrent > 0 ? bd.maxChargeCurrent / 1000 : 0 }, LOCAL);
          }
        }
      }
      await sleep(700);
    }
    if (!alive()) return;
    close();
    setR('t_charge', 'قياس الشحن', 'bad', phase === 0 ? 'الشاحن ظل موصول؛ ما تمكن من البدء' : 'ما اكتشف أي شاحن خلال 60 ثانية', 'جرّب كيبل وشاحن ثاني، ونظّف منفذ الشحن بعود خشبي؛ إذا استمر فمنفذ الشحن تالف.');
  };
  TESTS.batteryDeep = async function () {
    autoScreen('فحص صحة البطارية', 'جاري قراءة بيانات البطارية من النظام...');
    if (!DD || !DD.batteryDeep) { amsg('يحتاج نسخة APK الأصلية'); return; }
    var bd; try { bd = await DD.batteryDeep(); } catch (e) { close(); return setR('bh', 'صحة البطارية', 'info', 'تعذّرت القراءة: ' + (e.message || e)); }
    bar(100); await sleep(300); if (!alive()) return;
    close(); evalBattery(bd.battery, LOCAL); evalBatteryDeep(bd, LOCAL);
  };
  TESTS.loadTest = async function () {
    autoScreen('اختبار الحمل (بطارية + معالج)', 'افصل الشاحن! سيشتغل المعالج بأقصى طاقة 20 ثانية لقياس ضعف البطارية والحرارة. لا تقفل الشاشة.');
    if (!DD || !DD.loadTest) { amsg('يحتاج نسخة APK الأصلية'); return; }
    var tick = setInterval(function () { var l = $('#lvl'); if (l) l.style.width = Math.min(100, (parseFloat(l.style.width) || 0) + 100 / 27) + '%'; }, 1000);
    var r; try { r = await DD.loadTest({ seconds: 20 }); } catch (e) { clearInterval(tick); if (alive()) { close(); setR('bir', 'اختبار الحمل', 'warn', 'فشل: ' + (e.message || e)); } return; }
    clearInterval(tick); if (!alive()) return;
    close(); evalLoad(r, LOCAL);
  };
  TESTS.ramTest = async function () {
    autoScreen('اختبار الرام', 'جاري كتابة وقراءة أنماط على الذاكرة (قد ياخذ حتى نص دقيقة)...');
    await sleep(150);
    if (DD && DD.ramTest) {
      try {
        var r = await DD.ramTest({ mb: 512 }); if (!alive()) return; close(); return evalRam(r, LOCAL);
      } catch (e) { /* نكمل بالاختبار البديل */ }
    }
    var pats = [0xAAAAAAAA | 0, 0x55555555, 0xFFFFFFFF | 0, 0], errs = 0, n = 16 * 1024 * 1024, a;
    try { a = new Int32Array(n); } catch (e) { close(); return setR('t_ram', 'اختبار الرام', 'warn', 'ما انحجزت ذاكرة كافية للاختبار', 'أغلق التطبيقات وأعد المحاولة.'); }
    for (var p = 0; p < pats.length && alive(); p++) {
      var v = pats[p]; a.fill(v);
      for (var i = 0; i < n; i++) if (a[i] !== v) errs++;
      bar((p + 1) * 25); await sleep(30);
    }
    a = null;
    if (!alive()) return;
    close();
    setR('t_ram', 'اختبار الرام', errs ? 'bad' : 'ok', errs ? errs + ' خطأ بالذاكرة' : 'نجح (64 ميغا × 4 أنماط بدون أخطاء). هذا يفحص جزء من الرام فقط', errs ? 'أخطاء بالرام تسبب تعليق وإعادة تشغيل؛ الرام أو اللوحة الأم تالفة.' : '');
  };
  function evalRam(r, P) {
    var st = r.errors > 0 ? 'bad' : 'ok';
    setR(P.k + 't_ram', P.n + 'اختبار الرام', st, (r.errors > 0 ? r.errors + ' خطأ كتابة/قراءة!' : 'نجح بدون أخطاء') + ' | فُحص ' + r.testedMb + ' ميغا بـ5 أنماط (منها اختبار عناوين) | سرعة كتابة ≈ ' + Math.round(r.writeMBs) + ' / قراءة ≈ ' + Math.round(r.readMBs) + ' MB/s',
      r.errors > 0 ? 'أخطاء بالرام مؤكدة: تسبب تعليق وإعادة تشغيل وانهيار تطبيقات؛ الرام أو اللوحة الأم تالفة (إصلاح بالورشة).' : 'الفحص من داخل أندرويد يغطي جزء من الرام فقط؛ إذا عندك تعليق متكرر فالمشكلة ممكن ببقية الذاكرة أو بالتخزين.');
    if (r.swapTotalKb > 0) {
      var u = 100 - r.swapFreeKb * 100 / r.swapTotalKb, s2 = u > 80 ? 'warn' : 'ok';
      setR(P.k + 'swap', P.n + 'ذاكرة التبديل (Swap/ZRAM)', s2, 'مستخدمة ' + u.toFixed(0) + '% من ' + fmt(r.swapTotalKb * 1024), s2 === 'ok' ? '' : 'الجهاز يعاني ضغط ذاكرة: أغلق التطبيقات الثقيلة؛ إذا دائمًا هيك فالرام قليلة على استخدامك.');
    }
    if (r.lowMemory) setR(P.k + 'lowmem', P.n + 'نقص ذاكرة', 'warn', 'النظام معلن أنه بحالة ذاكرة منخفضة وقت الفحص', 'أغلق التطبيقات وأعد الفحص.');
  }
  TESTS.storageTest = async function () {
    autoScreen('اختبار التخزين', 'جاري كتابة وقراءة 32 ميغا وفحصها...');
    if (!DD || !DD.storageTest) { amsg('يحتاج نسخة APK الأصلية'); return; }
    try {
      var r = await DD.storageTest(); if (!alive()) return; close();
      evalStorageObj(r, LOCAL, 't_storage');
    } catch (e) { if (alive()) { close(); setR('t_storage', 'اختبار التخزين', 'bad', 'فشل الاختبار: ' + (e.message || e), 'خطأ كتابة/قراءة يدل على تخزين معطوب أو ممتلئ.'); } }
  };

  var FULL = ['touch', 'screen', 'backlight', 'camBack', 'camFront', 'flash', 'speaker', 'loud', 'earpiece', 'mic', 'vibrate', 'sensors', 'batteryTest', 'batteryDeep', 'loadTest', 'chargeTest', 'ramTest', 'storageTest'];
  async function fullScan() {
    seq = { list: FULL, i: 0 };
    await quick(true);
    seqNext();
  }

  function camTest(facing, label) {
    return async function () {
      open('<video id="vid" autoplay playsinline muted></video>' + BAR);
      $('#hint').textContent = 'هل الصورة واضحة وبدون خطوط أو بقع؟';
      var key = 't_cam_' + facing, name = label;
      try {
        var stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: { ideal: facing } } });
        $('#vid').srcObject = stream;
        cleanup = function () { stream.getTracks().forEach(function (t) { t.stop(); }); };
        verdict(key, name, 'تعمل والصورة واضحة', 'الصورة فيها مشكلة (ضبابية/بقع/خطوط)', 'نظّف العدسة؛ إذا استمرت المشكلة فالكاميرا تالفة.');
      } catch (e) {
        close();
        setR(key, name, 'bad', 'تعذّر فتح الكاميرا: ' + (e.name || e), 'NotAllowedError = لم تمنح الإذن. NotFoundError/NotReadableError = الكاميرا غير موجودة أو معطلة.');
      }
    };
  }
  TESTS.camBack = camTest('environment', 'الكاميرا الخلفية');
  TESTS.camFront = camTest('user', 'الكاميرا الأمامية');


  /* ───────── فحص جهاز آخر عبر الواي فاي ───────── */
  var HTTP = null;
  try { HTTP = window.Capacitor && Capacitor.Plugins && Capacitor.Plugins.CapacitorHttp || null; } catch (e) {}
  async function httpGet(url, timeout, ctimeout) {
    if (HTTP) {
      var r = await HTTP.get({ url: url, connectTimeout: ctimeout || 6000, readTimeout: timeout || 130000 });
      var d = r.data;
      if (typeof d === 'string') { try { d = JSON.parse(d); } catch (e) { d = {}; } }
      if (r.status >= 400) throw { status: r.status, data: d };
      return d;
    }
    var ctl = new AbortController(), t = setTimeout(function () { ctl.abort(); }, timeout || 130000);
    var rs = await fetch(url, { signal: ctl.signal });
    clearTimeout(t);
    if (!rs.ok) throw { status: rs.status };
    return rs.json();
  }

  async function remoteScan() {
    var ip = $('#rip').value.trim(), pin = $('#rpin').value.trim(), st = $('#rstat'), btn = $('#rscan');
    if (!/^\d{1,3}(\.\d{1,3}){3}$/.test(ip)) { st.textContent = 'اكتب عنوان IP صحيح مثل 192.168.1.20'; return; }
    var base = 'http://' + ip + ':8765', p;
    btn.disabled = true; st.textContent = '... جاري الاتصال';
    try { p = await httpGet(base + '/ping', 8000); }
    catch (e) {
      st.textContent = 'ما اتصل. تأكد أن الجهازين على نفس الواي فاي، وأن «وضع الاستقبال» شغال هناك، وأن Firewall يسمح (على الكمبيوتر).';
      btn.disabled = false; return;
    }
    var isPc = p.type === 'pc';
    var P = { k: 'r_' + p.type + '_', n: '[' + (isPc ? 'كمبيوتر' : 'موبايل 2') + (p.name ? ': ' + p.name : '') + '] ' };
    Array.from(R.keys()).forEach(function (k) { if (k.indexOf('r_') === 0) R.delete(k); });
    st.textContent = '... جاري الفحص' + (isPc ? ' (قد ياخذ دقيقة)' : '');
    var d;
    try { d = await httpGet(base + (isPc ? '/scan' : '/report') + '?pin=' + encodeURIComponent(pin), 300000); }
    catch (e) {
      st.textContent = (e && e.status === 403) ? 'رمز PIN غلط.' : (e && e.status === 429) ? 'الجهاز مشغول بفحص ثاني، حاول بعد شوي.' : 'فشل الفحص أو انقطع الاتصال.';
      btn.disabled = false; return;
    }
    if (isPc) {
      (d.results || []).forEach(function (x) { setR(P.k + x.group + '_' + x.name, P.n + x.name, x.status, x.details, x.fix); });
    } else {
      evalBattery(d.battery, P); evalSystem(d.system, P); evalSensors(d.sensors, P); evalStorageObj(d.storage, P); evalBatteryDeep(d.batteryDeep, P);
    }
    st.textContent = 'تم فحص ' + (p.name || 'الجهاز') + '.';
    btn.disabled = false; goReport();
  }


  function evalStorageObj(r, P, key) {
    if (!r) return;
    var slow = r.syncIops > 0 && r.syncIops < 80, st = !r.ok ? 'bad' : (r.writeMBs < 10 || slow) ? 'warn' : 'ok';
    setR(P.k + (key || 'storage'), P.n + 'اختبار التخزين', st, (r.ok ? 'البيانات رجعت سليمة' : 'البيانات رجعت تالفة!') + ' | كتابة ' + r.writeMBs.toFixed(0) + ' MB/s' + (r.readMBs ? ' | قراءة ' + r.readMBs.toFixed(0) + ' MB/s (تقريبية)' : '') + (r.syncIops ? ' | كتابة عشوائية 4K ≈ ' + Math.round(r.syncIops) + ' عملية/ث' : ''),
      !r.ok ? 'التخزين فيه قطاعات تالفة؛ انسخ بياناتك فورًا.' : st === 'warn' ? 'التخزين بطيء (سبب شائع لبطء/تعليق الجهاز): ممتلئ أو قديم أو فيه خلايا متعبة.' : '');
  }

  /* ───────── تحويل مخرجات ADB إلى نفس شكل بيانات الفحص ───────── */
  function kv(text, key) {
    var m = new RegExp('^\\s*' + key + ':\\s*(.+?)\\s*$', 'im').exec(text || '');
    return m ? m[1] : null;
  }
  function parseAdb(d) {
    var out = { extra: [] }, props = {}, m, re = /\[([^\]]+)\]: \[([^\]]*)\]/g;
    while ((m = re.exec(d.props || ''))) props[m[1]] = m[2];
    var bt = d.battery || '', lvl = parseInt(kv(bt, 'level'), 10), scale = parseInt(kv(bt, 'scale'), 10) || 100;
    var present = kv(bt, 'present');
    if (isFinite(lvl)) {
      out.battery = {
        available: true, level: Math.round(lvl * 100 / scale), temperature: (parseInt(kv(bt, 'temperature'), 10) || 0) / 10,
        voltage: parseInt(kv(bt, 'voltage'), 10) || 0, health: parseInt(kv(bt, 'health'), 10) || 1, status: parseInt(kv(bt, 'status'), 10) || 1,
        plugged: /AC powered:\s*true/i.test(bt) ? 1 : /USB powered:\s*true/i.test(bt) ? 2 : /Wireless powered:\s*true/i.test(bt) ? 4 : 0,
        present: present === null ? true : present === 'true', technology: kv(bt, 'technology') || '',
        cycleCount: parseInt(kv(bt, 'Cycle count'), 10) || -1, chargeCounter: parseInt(kv(bt, 'Charge counter'), 10) || 0
      };
    } else out.battery = { available: false };
    var mt = /MemTotal:\s*(\d+)/.exec(d.mem || ''), ma = /MemAvailable:\s*(\d+)/.exec(d.mem || '');
    var df = /(\d+)\s+(\d+)\s+(\d+)\s+\d+%/.exec(d.df || '');
    var up = parseFloat(String(d.uptime || '').split(/\s+/)[0]);
    var sz = /Physical size:\s*(\d+)x(\d+)/.exec(d.wm || ''), dn = /Physical density:\s*(\d+)/.exec(d.wm || '');
    var th = /thermal status:?\s*(\d+)/i.exec(d.thermal || '');
    out.system = {
      ramTotal: mt ? +mt[1] * 1024 : 0, ramAvail: ma ? +ma[1] * 1024 : 0, diskTotal: df ? +df[1] * 1024 : 0, diskFree: df ? +df[3] * 1024 : 0,
      uptimeMs: isFinite(up) ? up * 1000 : 0, sdk: parseInt(props['ro.build.version.sdk'], 10) || 0, release: props['ro.build.version.release'] || '',
      model: props['ro.product.model'] || '', manufacturer: props['ro.product.manufacturer'] || '', patch: props['ro.build.version.security_patch'] || '',
      thermal: th ? +th[1] : -1, rooted: false, cores: parseInt(d.cpu, 10) || 0, hardware: props['ro.hardware'] || '', board: props['ro.product.board'] || '',
      abi: props['ro.product.cpu.abi'] || '', dispW: sz ? +sz[1] : 0, dispH: sz ? +sz[2] : 0, dpi: dn ? +dn[1] : 0
    };
    if (!out.system.ramTotal) out.system.ramTotal = 1, out.system.ramAvail = 1;
    var low = String(d.sensors || '').toLowerCase().split(/recent sensor events/)[0], list = [];
    [[1, /accelerometer/], [2, /magnetic/], [4, /gyroscope/], [5, /android\.sensor\.light|\blight\b/], [8, /proximity/]].forEach(function (t) { if (t[1].test(low)) list.push({ type: t[0] }); });
    out.sensors = list;
    var cam = /Number of camera devices:\s*(\d+)/i.exec(d.camera || '');
    if (cam) out.extra.push(['cam', 'عدد الكاميرات', +cam[1] === 0 ? 'bad' : 'info', cam[1] + ' كاميرا ظاهرة للنظام', +cam[1] === 0 ? 'النظام ما يشوف أي كاميرا؛ الكاميرا أو كيبلها تالف.' : '']);
    var cr = parseInt(String(d.crashes || '').trim(), 10), an = parseInt(String(d.anr || '').trim(), 10);
    if (isFinite(cr)) out.extra.push(['crash', 'انهيارات التطبيقات (logcat)', cr > 10 ? 'warn' : 'ok', cr + ' انهيار بالسجل الأخير', cr > 10 ? 'انهيارات كثيرة: ذاكرة ضعيفة أو تطبيقات معطوبة أو تخزين فيه مشاكل.' : '']);
    if (isFinite(an)) out.extra.push(['anr', 'تعليق التطبيقات (ANR)', an > 5 ? 'warn' : 'ok', an + ' مرة', an > 5 ? 'تعليق متكرر: الجهاز بطيء أو تخزين/رام ضعيف.' : '']);
    if (d.dd) {
      var ex = /EXIT=(\d+)/.exec(d.dd), size = /\b33554432\b/.test(d.dd), mbs = d.ddMs ? 32000 / d.ddMs : 0;
      if (!ex || ex[1] !== '0') out.extra.push(['dd', 'اختبار التخزين', 'warn', 'تعذّر تنفيذ اختبار الكتابة من ADB (قد يكون ممنوعًا)', '']);
      else out.extra.push(['dd', 'اختبار التخزين', !size ? 'bad' : mbs < 8 ? 'warn' : 'ok', (size ? 'كتابة 32 ميغا نجحت' : 'الملف المكتوب حجمه غلط!') + ' | سرعة تقريبية ' + mbs.toFixed(0) + ' MB/s', !size ? 'التخزين تالف أو ممتلئ.' : mbs < 8 ? 'الكتابة بطيئة جدًا.' : '']);
    }
    var sysfs = {}; String(d.psy || '').split(/\r?\n/).forEach(function (l) { var m = /^(\w+)=(.+)$/.exec(l.trim()); if (m) sysfs[m[1]] = m[2]; });
    out.batteryDeep = { sysfs: sysfs, battery: out.battery, designMah: -1 };
    out.banner = d.usbName || props['ro.product.model'] || '';
    return out;
  }

  async function usbScan() {
    var st = $('#rstat'), btn = $('#rusb');
    if (!DD || !DD.adbUsbScan) { st.textContent = 'هذي الميزة تحتاج نسخة APK الأصلية.'; return; }
    btn.disabled = true; st.textContent = '... جاري البحث عن الجهاز الموصل. وافق على الرسائل اللي تظهر هنا وعلى الجهاز الثاني (ممكن ياخذ دقيقة)';
    var d;
    try { d = await DD.adbUsbScan(); }
    catch (e) {
      var msg = String((e && e.message) || e);
      st.textContent = msg === 'no_device' ? 'ما لقيت جهاز موصول. وصّله بكيبل OTG، وفعّل تصحيح USB عليه، وتأكد أن الكيبل يدعم نقل البيانات (مو شحن فقط).'
        : msg === 'no_permission' ? 'ما انمنح إذن الوصول لمنفذ USB. أعد المحاولة ووافق على الرسالة.'
        : msg.indexOf('auth') === 0 ? 'الجهاز الثاني ما وافق على تصحيح USB. أعد المحاولة ووافق على الرسالة اللي تطلع عليه (فعّل «السماح دائمًا»).'
        : 'فشل الاتصال بالجهاز: ' + msg;
      btn.disabled = false; return;
    }
    var p = parseAdb(d);
    var P = { k: 'r_usb_', n: '[موبايل 2' + (p.banner ? ': ' + p.banner : '') + '] ' };
    Array.from(R.keys()).forEach(function (k) { if (k.indexOf('r_') === 0) R.delete(k); });
    evalBattery(p.battery, P); evalSystem(p.system, P); evalSensors(p.sensors, P); if (p.batteryDeep) evalBatteryDeep(p.batteryDeep, P);
    p.extra.forEach(function (x) { setR(P.k + x[0], P.n + x[1], x[2], x[3], x[4]); });
    st.textContent = 'تم فحص الجهاز الموصل' + (p.banner ? ' (' + p.banner + ')' : '') + '.';
    btn.disabled = false; goReport();
  }

  async function discover() {
    var st = $('#rstat'), btn = $('#rfind');
    if (!DD || !DD.localIps) { st.textContent = 'البحث التلقائي يحتاج نسخة APK الأصلية. اكتب الـIP يدويًا.'; return; }
    btn.disabled = true; st.textContent = '... جاري البحث على الشبكة';
    var ips = []; try { ips = (await DD.localIps()).ips || []; } catch (e) {}
    var cands = [], seen = {}, mine = {};
    ips.forEach(function (x) { mine[x.ip] = 1; });
    ips.forEach(function (x) {
      var b = x.ip.split('.').slice(0, 3).join('.');
      [1, 129].concat(Array.from({ length: 254 }, function (_, i) { return i + 1; })).forEach(function (h) {
        var ip = b + '.' + h; if (!seen[ip] && !mine[ip]) { seen[ip] = 1; cands.push(ip); }
      });
    });
    var found = null;
    for (var i = 0; i < cands.length && !found; i += 40) {
      var batch = cands.slice(i, i + 40);
      var res = await Promise.all(batch.map(function (ip) { return httpGet('http://' + ip + ':8765/ping', 900, 700).then(function (p) { return { ip: ip, p: p }; }, function () { return null; }); }));
      found = res.filter(Boolean)[0] || null;
    }
    btn.disabled = false;
    if (found) { $('#rip').value = found.ip; st.textContent = 'لقيت: ' + found.ip + (found.p && found.p.name ? ' (' + found.p.name + ')' : '') + '. اكتب الـPIN واضغط افحص.'; }
    else st.textContent = 'ما لقيت جهاز. تأكد أن وضع الاستقبال شغال هناك وأن الجهازين على نفس الشبكة (أو أن USB tethering مفعّل).';
  }

  function setMethod(usb) {
    $('#paneNet').hidden = usb; $('#paneUsb').hidden = !usb;
    $('#mNet').className = usb ? 'ghost' : 'on'; $('#mUsb').className = usb ? 'on' : 'ghost';
    $('#rstat').textContent = '';
  }
  $('#mNet').onclick = function () { setMethod(false); };
  $('#mUsb').onclick = function () { setMethod(true); };
  $('#rusb').onclick = usbScan;
  $('#rfind').onclick = discover;

  var serving = false;
  async function toggleServe() {
    var b = $('#rserve'), info = $('#rinfo');
    if (!DD) { info.hidden = false; info.innerHTML = '<div class="d">هذي الميزة تحتاج نسخة APK الأصلية.</div>'; return; }
    try {
      if (serving) {
        await DD.stopServer(); serving = false; b.textContent = 'تشغيل وضع الاستقبال'; info.hidden = true; return;
      }
      var r = await DD.startServer({ port: 8765 });
      serving = true; b.textContent = 'إيقاف وضع الاستقبال'; info.hidden = false;
      info.innerHTML = '<b>✔ جاهز للفحص</b><div class="d" dir="ltr" style="text-align:right">IP: ' + esc(r.ip || '؟') + '<br>PIN: ' + esc(r.pin) + '</div><div class="f">خلّ التطبيق مفتوح والشاشة شغالة، والجهازين على نفس الواي فاي.</div>';
    } catch (e) {
      info.hidden = false; info.innerHTML = '<div class="d">تعذّر التشغيل: ' + esc((e && e.message) || e) + '</div>';
    }
  }
  $('#rscan').onclick = remoteScan;
  $('#rserve').onclick = toggleServe;


  /* ───────── مساعد الأعطال ───────── */
  var USBTIP = 'افحص بفلاشة الفحص (الدليل أعلاه): تكشف حالة القرص والرام والحرارة وسبب الشاشة الزرقاء وملفات إقلاع ويندوز بدون ما يقلع الجهاز.';
  var WIZ = {
    start: { q: 'شنو حالة الجهاز؟', o: [
      ['ما يشتغل أبدًا (لا مراوح ولا لمبات)', 'dead'], ['يشتغل (مراوح/لمبات) بس الشاشة سوداء', 'black'],
      ['يطلّع صوت بيب', 'beep'], ['يوقف عند شعار الشركة أو يطلع رسالة خطأ', 'boot'],
      ['يدخل ويندوز بس يعلّق / ريستارت / شاشة زرقاء', 'win'], ['يطفي لحاله أو يسخن', 'hot'], ['لابتوب ما يشحن', 'charge']] },
    dead: { q: 'لما تضغط زر التشغيل، شنو يصير؟', o: [['ولا شي أبدًا', 'dead2'], ['يشتغل ثانية ثم ينطفي', 'r_short'], ['لمبة صغيرة تضوي بس ما يقلع', 'r_standby']] },
    dead2: { q: 'جرّب كيبل ومقبس كهرباء ثاني، وتأكد من مفتاح الباور خلف الجهاز. صار يشتغل؟', o: [['إي اشتغل', 'r_cable'], ['نفس الشي', 'dead3']] },
    dead3: { q: 'نوع الجهاز؟', o: [['كمبيوتر مكتبي', 'r_psu'], ['لابتوب', 'r_lap_dead']] },
    black: { q: 'هل يطلّع صوت بيب عند التشغيل؟', o: [['إي يبيّب', 'beep'], ['لا', 'black2']] },
    black2: { q: 'جرّب شاشة أو كيبل ثاني (HDMI/DP). ظهرت صورة؟', o: [['إي ظهرت', 'r_display'], ['لا', 'black3']] },
    black3: { q: 'الجهاز عنده كرت شاشة منفصل؟', o: [['إي كرت منفصل', 'r_gpu'], ['لا / لابتوب', 'r_ram']] },
    beep: { q: 'شلون شكل البيب؟', o: [['بيب قصير واحد ثم يقلع طبيعي', 'r_beep_ok'], ['بيب طويل متكرر', 'r_ram'],
      ['بيب طويل + 2 أو 3 قصير', 'r_gpu'], ['بيب سريع متواصل', 'r_beep_hot'], ['بيب قصير متكرر', 'r_psu']] },
    boot: { q: 'شنو يظهر بالضبط؟', o: [
      ['No bootable device / Boot device not found / Operating system not found', 'r_nodisk'],
      ['شعار الشركة ويعلّق أو يدور دائمًا', 'r_logo'], ['CMOS error / CMOS checksum / Date and time not set', 'r_cmos'],
      ['Preparing Automatic Repair أو Startup Repair يتكرر', 'r_repair']] },
    win: { q: 'شنو يصير بالضبط؟', o: [['شاشة زرقاء (BSOD)', 'bsod'], ['ريستارت لحاله', 'hot'], ['يعلّق أو بطيء جدًا', 'r_slow']] },
    bsod: { q: 'سجّل الكود أو الاسم اللي يظهر بالشاشة الزرقاء (مثل MEMORY_MANAGEMENT أو 0x1A) واكتبه بخانة «كود الشاشة الزرقاء» بالأسفل. ما تعرف الكود؟', o: [['ما أعرفه / تختفي بسرعة', 'r_bsod_unknown']] },
    hot: { q: 'متى ينطفي؟', o: [['بعد دقائق من التشغيل أو وقت الضغط/لعبة', 'r_heat'], ['عشوائي بدون سبب واضح', 'r_random']] },
    charge: { q: 'جرّب شاحن ثاني بنفس المواصفات. شحن؟', o: [['إي', 'r_charger'], ['لا', 'r_chport']] }
  };
  var WRES = {
    r_short: { t: 'يشتغل ثانية وينطفي', c: ['حماية الباور سبلاي (قصر أو حمل زايد)', 'رام أو كرت شاشة مو مثبّت زين', 'قصر في اللوحة الأم'],
      s: ['فصّل كل شي ما عدا المعالج وشريحة رام وحدة والباور', 'اطلع الرام وارجعها بقوة، وجرّب كل سلوط', 'فصّل كابلات اللوحة الأمامية (زر/USB) وجرّب', 'جرّب باور سبلاي ثاني إذا متوفر'] },
    r_standby: { t: 'لمبة تضوي بس ما يقلع', c: ['زر الباور أو كيبله', 'الباور سبلاي ضعيف', 'اللوحة الأم'],
      s: ['اتصل بدبوسين PWR_SW على اللوحة بمفك لتشغيله مباشرة (يثبت إذا الزر خربان)', 'جرّب باور ثاني', 'اطلع بطارية CMOS دقيقتين وارجعها'] },
    r_cable: { t: 'كيبل أو مقبس الكهرباء', c: ['الكيبل أو المقبس أو مفتاح الباور خلف الجهاز'], s: ['استعمل كيبل ومقبس سليمين'] },
    r_psu: { t: 'مكتبي ما يشتغل أبدًا', c: ['الباور سبلاي تالف (الأرجح)', 'زر الباور أو كيبله', 'اللوحة الأم'],
      s: ['جرّب باور سبلاي ثاني سليم', 'افحص مفتاح الباور خلف الجهاز', 'جرّب تشغيل اللوحة بدبوسي PWR_SW', 'إذا بقي ميت: اللوحة الأم'] },
    r_lap_dead: { t: 'لابتوب ما يشتغل', c: ['الشاحن أو منفذ الشحن', 'بطارية ميتة أو منتفخة', 'اللوحة الأم'],
      s: ['جرّب شاحن ثاني بنفس المواصفات', 'فصّل الشاحن والبطارية (إن كانت تنفصل) واضغط زر التشغيل 30 ثانية ثم أعد التوصيل', 'افحص منفذ الشحن عن تلف أو حرارة'] },
    r_display: { t: 'الشاشة أو الكيبل', c: ['الشاشة أو كيبل HDMI/DP تالف', 'مصدر الإدخال (Input) غلط على الشاشة'], s: ['غيّر الكيبل والمنفذ', 'اختر المصدر الصحيح بالشاشة'] },
    r_gpu: { t: 'مشكلة بكرت الشاشة (أو البيب يدل عليه)', c: ['كرت الشاشة غير مثبّت أو تماسه وسخ', 'وصلة الطاقة الإضافية للكرت', 'الكرت نفسه تالف'],
      s: ['اطلع الكرت ونظّف التماس بممحاة وارجعه', 'تأكد من وصلة الطاقة (6/8 pin)', 'وصّل الشاشة بمنفذ اللوحة الأم إن وجد', 'جرّب كرت ثاني'] },
    r_ram: { t: 'الرام غالبًا (أو سلوطها)', c: ['شريحة رام وسخة أو غير مثبّتة', 'شريحة رام تالفة أو سلوط تالف', 'بطارية CMOS أو إعدادات BIOS', 'معالج أو لوحة (أقل احتمال)'],
      s: ['اطلع الرام ونظّف التماس بممحاة وارجعها بقوة', 'جرّب شريحة وحدة وكل سلوط على حدة', 'اطلع بطارية CMOS 5 دقايق وارجعها', 'اختبر الرام بـMemtest86+ من الفلاشة إذا قلع الجهاز'] },
    r_beep_ok: { t: 'بيب قصير واحد', c: ['هذا الطبيعي: يعني اجتاز فحص POST'], s: ['إذا ما يقلع بعدها فالمشكلة بالقرص أو الإقلاع: جرّب فلاشة الفحص'] },
    r_beep_hot: { t: 'بيب سريع متواصل', c: ['سخونة المعالج أو مروحته متوقفة', 'مشكلة بالباور'], s: ['افحص مروحة المعالج تدور', 'نظّف التراب وغيّر المعجون الحراري', 'جرّب باور ثاني'] },
    r_nodisk: { t: 'ما يشوف نظام التشغيل أو القرص', c: ['القرص ما معترف به (كيبل SATA/NVMe أو تلف)', 'ترتيب الإقلاع/UEFI-Legacy بالـBIOS', 'ملفات الإقلاع تالفة أو قسم ويندوز تالف'],
      s: ['ادخل BIOS: هل يظهر القرص؟ (إذا لا → كيبل/قرص تالف)', 'غيّر كيبل ومنفذ SATA', 'تأكد أن القرص هو أول خيار إقلاع', 'افحص بفلاشة الفحص لمعرفة حالة القرص وملفات الإقلاع'], usb: true },
    r_logo: { t: 'يعلّق عند الشعار', c: ['قرص فيه قطاعات تالفة', 'رام تالفة', 'تحديث ويندوز فاشل أو ملفات تالفة'],
      s: ['افصل كل الأجهزة الخارجية وجرّب', 'اطلع الرام وجرّب شريحة شريحة', 'افحص القرص بفلاشة الفحص'], usb: true },
    r_cmos: { t: 'رسالة CMOS أو الوقت', c: ['بطارية CMOS (CR2032) ضعيفة'], s: ['غيّر بطارية CMOS بالجديدة', 'اضبط التاريخ والإعدادات بالـBIOS'] },
    r_repair: { t: 'يدخل Startup Repair بدون فايدة', c: ['ملفات إقلاع ويندوز تالفة', 'قرص فيه أخطاء', 'تحديث أو تعريف فاسد'],
      s: ['افحص القرص بفلاشة الفحص (تقرأ سجل Startup Repair بنفسها)', 'جرّب Startup Repair/System Restore من قرص تثبيت ويندوز', 'انسخ بياناتك قبل أي إعادة تثبيت'], usb: true },
    r_slow: { t: 'بطيء أو يعلّق', c: ['القرص (خصوصًا HDD) فيه مشاكل أو شبه ممتلئ', 'رام قليلة أو تالفة', 'حرارة عالية تخفّض الأداء', 'فيروسات أو برامج كثيرة بالإقلاع'],
      s: ['شغّل الفحص السريع/برنامج الكمبيوتر وشوف حالة SMART', 'نظّف المراوح وافحص الحرارة', 'قلّل برامج بدء التشغيل'], usb: true },
    r_bsod_unknown: { t: 'شاشة زرقاء بدون كود', c: ['رام أو قرص أو تعريف (الأشيع)', 'حرارة أو باور'],
      s: ['عطّل الريستارت التلقائي: System Properties ← Advanced ← Startup and Recovery', 'شغّل برنامج الكمبيوتر (EXE): يقرأ آخر أكواد الشاشة الزرقاء من السجل', 'أو افحص بالفلاشة، تقرأ ملفات minidump من القرص'], usb: true },
    r_heat: { t: 'ينطفي مع السخونة', c: ['تراب بالمراوح والمشتتات', 'معجون حراري ناشف', 'مروحة معطّلة'], s: ['نظّف التراب بهواء مضغوط', 'غيّر المعجون الحراري', 'افحص دوران المراوح', 'راقب الحرارة ببرنامج الكمبيوتر أو الفلاشة (اختبار ضغط 25 ثانية)'], usb: true },
    r_random: { t: 'ينطفي عشوائيًا', c: ['باور سبلاي ضعيف (الأرجح)', 'رام أو لوحة أم', 'بطارية CMOS', 'كيبلات سائبة'], s: ['جرّب باور ثاني', 'اعد تثبيت الرام والكيبلات', 'راقب سجلات Kernel-Power بالبرنامج'], usb: true },
    r_charger: { t: 'الشاحن تالف', c: ['الشاحن أو كيبله'], s: ['استبدل الشاحن بمواصفات مطابقة'] },
    r_chport: { t: 'لابتوب ما يشحن مع شاحن سليم', c: ['منفذ الشحن مكسور', 'بطارية ميتة', 'دائرة الشحن باللوحة'], s: ['حرّك الكيبل بالمنفذ وشوف إذا يتقطع', 'جرّب تشغيله بدون بطارية', 'قرّر بين تبديل المنفذ أو البطارية عند فني'] }
  };
  var BSODS = [
    [0x0A, 'IRQL_NOT_LESS_OR_EQUAL', 'تعريف أو رام', 'ارجع/حدّث آخر تعريف ثبتّه، واختبر الرام'],
    [0x19, 'BAD_POOL_HEADER', 'رام أو تعريف', 'اختبر الرام وحدّث التعريفات'],
    [0x1A, 'MEMORY_MANAGEMENT', 'الرام غالبًا', 'اختبر الرام بـMemtest86+ وجرّب الشرائح واحدة واحدة'],
    [0x1E, 'KMODE_EXCEPTION_NOT_HANDLED', 'تعريف', 'حدّث أو احذف آخر تعريف'],
    [0x24, 'NTFS_FILE_SYSTEM', 'القرص أو نظام الملفات', 'افحص القرص (SMART) وشغّل chkdsk'],
    [0x3B, 'SYSTEM_SERVICE_EXCEPTION', 'تعريف أو ملفات نظام', 'حدّث التعريفات وشغّل sfc /scannow'],
    [0x50, 'PAGE_FAULT_IN_NONPAGED_AREA', 'رام أو قرص أو تعريف', 'اختبر الرام والقرص'],
    [0x7A, 'KERNEL_DATA_INPAGE_ERROR', 'القرص أو كيبله أو الرام', 'افحص القرص (SMART) وغيّر كيبل SATA'],
    [0x7B, 'INACCESSIBLE_BOOT_DEVICE', 'القرص أو وضع SATA (AHCI/RAID) أو تعريفه', 'تحقق من القرص والـBIOS، وافحص بالفلاشة'],
    [0x9C, 'MACHINE_CHECK_EXCEPTION', 'عتاد: معالج أو حرارة', 'افحص الحرارة والمعالج'],
    [0x9F, 'DRIVER_POWER_STATE_FAILURE', 'تعريف أو إدارة الطاقة', 'حدّث التعريفات وعطّل الإيقاف الذكي لـUSB'],
    [0xC2, 'BAD_POOL_CALLER', 'تعريف أو رام', 'حدّث التعريفات واختبر الرام'],
    [0xD1, 'DRIVER_IRQL_NOT_LESS_OR_EQUAL', 'تعريف', 'حدّث أو ارجع التعريف المسبب'],
    [0xED, 'UNMOUNTABLE_BOOT_VOLUME', 'نظام ملفات القرص تالف', 'chkdsk من قرص التثبيت، وافحص القرص بالفلاشة'],
    [0xEF, 'CRITICAL_PROCESS_DIED', 'ملفات نظام أو قرص', 'افحص القرص وشغّل sfc /scannow من وضع الاسترداد'],
    [0x101, 'CLOCK_WATCHDOG_TIMEOUT', 'معالج أو حرارة أو BIOS', 'حدّث BIOS وافحص الحرارة'],
    [0x109, 'CRITICAL_STRUCTURE_CORRUPTION', 'رام أو تعريف', 'اختبر الرام'],
    [0x116, 'VIDEO_TDR_FAILURE', 'كرت الشاشة أو تعريفه', 'حدّث/أعد تثبيت تعريف الكرت وافحص حرارته'],
    [0x117, 'VIDEO_TDR_TIMEOUT_DETECTED', 'كرت الشاشة', 'حدّث تعريف الكرت وافحص حرارته'],
    [0x124, 'WHEA_UNCORRECTABLE_ERROR', 'عتاد: معالج أو رام أو باور أو لوحة', 'اختبر الرام، افحص الحرارة والباور'],
    [0x133, 'DPC_WATCHDOG_VIOLATION', 'تعريف (غالبًا SSD) أو قرص', 'حدّث فيرموير/تعريف القرص']
  ];
  var wizStack = ['start'];

  function wizRender() {
    var id = wizStack[wizStack.length - 1], box = $('#wq');
    if (WIZ[id]) {
      box.innerHTML = '<div class="card"><b>' + esc(WIZ[id].q) + '</b></div>' + WIZ[id].o.map(function (o, i) {
        return '<button class="opt" data-i="' + i + '">' + esc(o[0]) + '</button>';
      }).join('');
      box.querySelectorAll('.opt').forEach(function (b) {
        b.onclick = function () { wizStack.push(WIZ[id].o[b.dataset.i][1]); wizRender(); };
      });
    } else {
      var r = WRES[id];
      box.innerHTML = '<div class="card warn"><b>🔎 ' + esc(r.t) + '</b>' +
        '<div class="d"><b>الأسباب المحتملة (من الأرجح):</b><ul>' + r.c.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>' +
        '<div class="d"><b>شنو تسوي:</b><ul>' + r.s.map(function (x) { return '<li>' + esc(x) + '</li>'; }).join('') + '</ul></div>' +
        (r.usb ? '<div class="f">💡 ' + esc(USBTIP) + '</div>' : '') + '</div>' +
        '<button class="big ghost" id="wadd">➕ أضف للتقرير</button>';
      $('#wadd').onclick = function () {
        setR('wiz_' + id, 'مساعد الأعطال: ' + r.t, 'warn', 'الأسباب المحتملة: ' + r.c.join(' ، '), r.s.join(' • '));
        $('#wadd').textContent = '✔ أُضيف للتقرير';
      };
    }
  }
  $('#wback').onclick = function () { if (wizStack.length > 1) wizStack.pop(); wizRender(); };
  $('#wreset').onclick = function () { wizStack = ['start']; wizRender(); };

  $('#bsodGo').onclick = function () {
    var q = $('#bsod').value.trim().toLowerCase(), out = $('#bsodOut');
    if (!q) { out.innerHTML = ''; return; }
    var num = /^0x[0-9a-f]+$/.test(q) ? parseInt(q, 16) : null;
    var hit = BSODS.filter(function (b) { return (num !== null && b[0] === num) || b[1].toLowerCase().indexOf(q.replace(/\s+/g, '_')) >= 0; });
    out.innerHTML = hit.length ? hit.map(function (b) {
      return '<div class="card warn"><b dir="ltr" style="text-align:right">' + esc(b[1]) + ' (0x' + b[0].toString(16).toUpperCase() + ')</b>' +
        '<div class="d">السبب الأرجح: ' + esc(b[2]) + '</div><div class="f">' + esc(b[3]) + '</div></div>';
    }).join('') : '<div class="card"><div class="d">ما لقيت هذا الكود بالقائمة. اعمل فحص بالفلاشة أو برنامج الكمبيوتر حتى يقرأه من السجل.</div></div>';
  };
  wizRender();

  $('#runAll').onclick = fullScan;
  document.querySelectorAll('[data-test]').forEach(function (b) { b.onclick = function () { TESTS[b.dataset.test](); }; });
  $('#runQuick').onclick = quick;
  $('#copy').onclick = function () {
    var t = $('#txt'); t.select();
    try { navigator.clipboard.writeText(t.value); } catch (e) { document.execCommand('copy'); }
    $('#copy').textContent = '✔ تم النسخ'; setTimeout(function () { $('#copy').textContent = '📋 نسخ التقرير'; }, 1500);
  };
  $('#clear').onclick = function () { R.clear(); try { localStorage.removeItem('dd_results'); } catch (e) {} render(); };
  render();
})();
