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

  async function quick() {
    var btn = $('#runQuick');
    btn.disabled = true; btn.textContent = '... جاري الفحص';
    if (!DD) setR('native', 'الوصول للنظام', 'warn', 'التطبيق يعمل بدون الجزء الأصلي (غالبًا فتحته من المتصفح).', 'ثبّت نسخة APK الأصلية.');
    else {
      try { await batteryCheck(); } catch (e) { setR('battery', 'البطارية', 'info', 'تعذّرت القراءة'); }
      try { await systemCheck(); } catch (e) { setR('system', 'النظام', 'info', 'تعذّرت القراءة'); }
      try { await sensorList(); } catch (e) { setR('sensorlist', 'الحساسات', 'info', 'تعذّرت القراءة'); }
    }
    await networkCheck();
    await mediaCount();
    setR('screen', 'معلومات الشاشة', 'info', screen.width + '×' + screen.height + ' | كثافة ' + (window.devicePixelRatio || 1) + ' | نقاط لمس ' + (navigator.maxTouchPoints || 0));
    btn.disabled = false; btn.textContent = '▶ أعد الفحص السريع';
    goReport();
  }

  async function batteryCheck() {
    var b = await DD.getBattery();
    if (!b.available) return setR('battery', 'البطارية', 'warn', 'ما تم اكتشاف بطارية');
    var st = 'ok', fix = '';
    if ([3, 4, 5, 6].indexOf(b.health) >= 0) { st = 'bad'; fix = 'البطارية معطوبة أو تسخن؛ افصل الشاحن واستبدلها.'; }
    else if ([1, 7].indexOf(b.health) >= 0) st = 'warn';
    if (b.temperature >= 45) { st = 'bad'; fix = 'الحرارة عالية جدًا؛ افصل الشاحن وأبعد الجهاز عن الحرارة.'; }
    else if (b.temperature >= 40 && st === 'ok') { st = 'warn'; fix = 'الحرارة مرتفعة نسبيًا.'; }
    if (b.present === false) { st = 'bad'; fix = 'النظام ما يشوف البطارية.'; }
    var d = 'الشحن ' + b.level + '% | الحالة: ' + (HEALTH[b.health] || b.health) + ' | الحرارة ' + b.temperature.toFixed(1) + '°C | الفولتية ' + b.voltage + ' mV' + (b.technology ? ' | ' + b.technology : '');
    setR('battery', 'البطارية', st, d, fix);
    if (b.cycleCount > 0) {
      var s2 = b.cycleCount > 800 ? 'bad' : b.cycleCount > 500 ? 'warn' : 'ok';
      setR('cycles', 'دورات الشحن', s2, b.cycleCount + ' دورة', s2 === 'ok' ? '' : 'البطارية قاربت نهاية عمرها.');
    } else setR('cycles', 'دورات الشحن', 'info', 'هذا الجهاز/الأندرويد لا يكشف عدد الدورات (يتوفر من أندرويد 14).');
  }

  async function systemCheck() {
    var s = await DD.getSystem();
    setR('device', 'معلومات الجهاز', 'info', s.manufacturer + ' ' + s.model + ' | أندرويد ' + s.release + ' | يعمل منذ ' + (s.uptimeMs / 86400000).toFixed(1) + ' يوم');
    var usedRam = 100 - s.ramAvail * 100 / s.ramTotal;
    setR('ram', 'الذاكرة RAM', usedRam > 90 ? 'warn' : 'ok', 'الإجمالي ' + fmt(s.ramTotal) + ' | مستخدم ' + usedRam.toFixed(0) + '%', usedRam > 90 ? 'أغلق التطبيقات الثقيلة.' : '');
    if (s.diskTotal) {
      var freePct = s.diskFree * 100 / s.diskTotal;
      var ds = freePct < 5 ? 'bad' : freePct < 15 ? 'warn' : 'ok';
      setR('disk', 'التخزين', ds, 'الإجمالي ' + fmt(s.diskTotal) + ' | الفارغ ' + fmt(s.diskFree) + ' (' + freePct.toFixed(0) + '%)', ds === 'ok' ? '' : 'امسح ملفات أو تطبيقات غير مهمة.');
    }
    if (s.thermal >= 0) {
      var ts = s.thermal >= 3 ? 'bad' : s.thermal === 2 ? 'warn' : 'ok';
      setR('thermal', 'الحرارة العامة', ts, 'المستوى ' + s.thermal + ' (0 طبيعي … 6 طوارئ)', ts === 'ok' ? '' : 'الجهاز يسخن: أغلق التطبيقات وأخرج الغطاء.');
    }
    if (s.patch) {
      var age = (Date.now() - new Date(s.patch).getTime()) / 86400000;
      if (isFinite(age)) setR('patch', 'تحديث الأمان', age > 365 ? 'warn' : 'ok', 'آخر باتش ' + s.patch, age > 365 ? 'النظام قديم أمنيًا؛ حدّثه إن توفر.' : '');
    }
    if (s.rooted) setR('root', 'صلاحيات Root', 'info', 'الجهاز يبدو مُجذَّرًا (Root).');
  }

  async function sensorList() {
    var r = await DD.getSensors(), types = {};
    r.sensors.forEach(function (x) { types[x.type] = true; });
    var missing = Object.keys(STYPES).filter(function (t) { return !types[t]; });
    if (!missing.length) setR('sensorlist', 'الحساسات الأساسية', 'ok', 'كلها موجودة (' + r.sensors.length + ' حساس إجمالًا)');
    missing.forEach(function (t) {
      var m = STYPES[t];
      setR('sens_' + m[0], 'حساس مفقود: ' + m[1], m[2], 'غير ظاهر للنظام', 'إذا الموديل يفترض فيه هذا الحساس فهو معطّل؛ بعض الموبايلات الاقتصادية ما فيها.');
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
  function open(html) { ov.innerHTML = html; ov.hidden = false; return ov; }
  function close() { if (cleanup) { try { cleanup(); } catch (e) {} cleanup = null; } ov.hidden = true; ov.innerHTML = ''; goReport(); }
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
          setR('t_' + t[1], 'حساس ' + t[2], st, 'قراءة: ' + v, fix); lines.push(t[2] + ': ' + v);
        }
        $('#sres') && ($('#sres').innerHTML = lines.map(esc).join('<br>'));
      }
    }
  };

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
