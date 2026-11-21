# -*- coding: utf-8 -*-
"""محرك الفحص (بدون واجهة) — يفحص الكمبيوتر (ويندوز) والموبايل (أندرويد عبر ADB)."""
import datetime
import glob
import html
import http.server
import json
import os
import re
import secrets
import shutil
import socket
import string
import struct
import subprocess
import sys
import tempfile
import threading
import time
import zlib
import urllib.parse
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET

OK, WARN, BAD, INFO = "ok", "warn", "bad", "info"
IS_WIN = os.name == "nt"
NO_WINDOW = 0x08000000 if IS_WIN else 0
_ORDER = {OK: 0, INFO: 0, WARN: 1, BAD: 2}


class Result:
    def __init__(self, group, name, status, details="", fix=""):
        self.group, self.name, self.status = group, name, status
        self.details, self.fix = details, fix


# ───────────────────────── أدوات عامة ─────────────────────────
def run(cmd, timeout=60):
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=timeout, creationflags=NO_WINDOW)
        return p.returncode, p.stdout.decode("utf-8", "replace"), p.stderr.decode("utf-8", "replace")
    except FileNotFoundError:
        return 127, "", "not found"
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"


def ps(script, timeout=90):
    full = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; $ProgressPreference='SilentlyContinue'; " + script)
    _, out, _ = run(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                     "-Command", full], timeout)
    return out.strip()


def ps_json(script, timeout=90):
    out = ps(script + " | ConvertTo-Json -Depth 4 -Compress", timeout)
    if not out:
        return []
    try:
        data = json.loads(out)
    except ValueError:
        return []
    return [data] if isinstance(data, dict) else (data or [])


def fmt_size(n):
    try:
        n = float(n)
    except (TypeError, ValueError):
        return "?"
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.0f} {u}" if u in ("B", "KB") else f"{n:.1f} {u}"
        n /= 1024


def is_admin():
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def safe(group, label, fn):
    try:
        return fn()
    except Exception as e:  # لا نوقف الفحص كله بسبب فحص واحد
        return [Result(group, label, WARN, f"تعذّر إجراء الفحص: {e}")]


# ───────────────────────── فحص الكمبيوتر ─────────────────────────
G_PC = "الكمبيوتر"


def analyze_disk(d):
    status, fix, notes = OK, "", []

    def up(s):
        nonlocal status
        if _ORDER[s] > _ORDER[status]:
            status = s

    health = str(d.get("Health") or "")
    notes.append(f"{d.get('Media') or ''} {fmt_size(d.get('Size'))} | الحالة: {health or 'غير معروفة'}")
    if health and health != "Healthy":
        up(BAD if health.lower() == "unhealthy" else WARN)
        fix = "انسخ ملفاتك المهمة فورًا ثم افحص القرص أو استبدله."
    t = d.get("Temp")
    if isinstance(t, (int, float)) and t > 0:
        notes.append(f"الحرارة {t}°C")
        if t >= 70:
            up(BAD)
        elif t >= 60:
            up(WARN)
    w = d.get("Wear")
    if isinstance(w, (int, float)) and w >= 0:
        notes.append(f"استهلاك الـSSD {w}%")
        if w >= 90:
            up(BAD)
            fix = fix or "عمر القرص شارف على النهاية، استبدله."
        elif w >= 70:
            up(WARN)
    if isinstance(d.get("Hours"), (int, float)):
        notes.append(f"ساعات التشغيل {int(d['Hours'])}")
    errs = (d.get("ReadErr") or 0) + (d.get("WriteErr") or 0)
    if errs > 0:
        notes.append(f"أخطاء قراءة/كتابة غير مصححة: {errs}")
        up(BAD)
        fix = fix or "أخطاء فعلية في القرص: انسخ بياناتك واستبدله."
    return status, " | ".join(notes), fix


def check_disks():
    script = ("Get-PhysicalDisk | ForEach-Object { $r = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
              "[pscustomobject]@{Name=$_.FriendlyName; Media=[string]$_.MediaType; Health=[string]$_.HealthStatus; "
              "Size=$_.Size; Temp=$r.Temperature; Wear=$r.Wear; Hours=$r.PowerOnHours; "
              "ReadErr=$r.ReadErrorsUncorrected; WriteErr=$r.WriteErrorsUncorrected} }")
    disks = ps_json(script)
    out = []
    if not disks:
        return [Result(G_PC, "الأقراص", WARN, "تعذّر قراءة معلومات الأقراص", "شغّل البرنامج كمسؤول (Run as administrator).")]
    for d in disks:
        st, det, fix = analyze_disk(d)
        out.append(Result(G_PC, f"القرص: {d.get('Name') or '?'}", st, det, fix))
    pred = ps_json("Get-CimInstance -Namespace root\\wmi -ClassName MSStorageDriver_FailurePredictStatus "
                   "-ErrorAction SilentlyContinue | Select-Object InstanceName,PredictFailure")
    if any(p.get("PredictFailure") for p in pred):
        out.append(Result(G_PC, "توقّع فشل القرص (SMART)", BAD, "SMART يتوقع فشل أحد الأقراص قريبًا",
                          "انسخ بياناتك حالًا واستبدل القرص."))
    elif pred:
        out.append(Result(G_PC, "توقّع فشل القرص (SMART)", OK, "لا توجد مؤشرات فشل"))
    return out


def check_space():
    rows = ps_json("Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,Size,FreeSpace")
    out = []
    for r in rows:
        size, free = r.get("Size") or 0, r.get("FreeSpace") or 0
        if not size:
            continue
        pct = free * 100 / size
        st = BAD if pct < 10 else WARN if pct < 20 else OK
        fix = "احذف ملفات غير ضرورية أو انقلها (المساحة الحرة قليلة وتبطّئ النظام)." if st != OK else ""
        out.append(Result(G_PC, f"مساحة {r.get('DeviceID')}", st,
                          f"الحجم {fmt_size(size)} | الفارغ {fmt_size(free)} ({pct:.0f}%)", fix))
    return out


def check_ram():
    os_ = ps_json("Get-CimInstance Win32_OperatingSystem | Select-Object TotalVisibleMemorySize,FreePhysicalMemory")
    mods = ps_json("Get-CimInstance Win32_PhysicalMemory | Select-Object Capacity,Speed")
    if not os_:
        return [Result(G_PC, "الذاكرة RAM", WARN, "تعذّرت القراءة")]
    tot, free = os_[0]["TotalVisibleMemorySize"] * 1024, os_[0]["FreePhysicalMemory"] * 1024
    used = (tot - free) * 100 / tot
    st = BAD if used > 95 else WARN if used > 85 else OK
    det = f"الإجمالي {fmt_size(tot)} | المستخدم {used:.0f}%"
    if mods:
        det += f" | الشرائح: {len(mods)}" + (f" بسرعة {mods[0].get('Speed')}MHz" if mods[0].get("Speed") else "")
    fix = "أغلق برامج الخلفية أو زِد الرام." if st != OK else ""
    out = [Result(G_PC, "الذاكرة RAM", st, det, fix)]
    out.append(Result(G_PC, "اختبار أخطاء الرام", INFO,
                      "الفحص العميق للرام يحتاج إعادة تشغيل",
                      "إذا عندك شاشة زرقاء أو تعليق: اكتب mdsched.exe في Run وأعد التشغيل لاختبار الرام."))
    return out


def check_cpu():
    cpu = ps_json("Get-CimInstance Win32_Processor | Select-Object Name,NumberOfCores,NumberOfLogicalProcessors,LoadPercentage")
    if not cpu:
        return [Result(G_PC, "المعالج", WARN, "تعذّرت القراءة")]
    c = cpu[0]
    st, fix = OK, ""
    det = f"{(c.get('Name') or '').strip()} | {c.get('NumberOfCores')} أنوية / {c.get('NumberOfLogicalProcessors')} خيوط | الحمل {c.get('LoadPercentage')}%"
    temps = ps_json("Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
                    "-ErrorAction SilentlyContinue | Select-Object CurrentTemperature")
    vals = [t["CurrentTemperature"] / 10 - 273.15 for t in temps if t.get("CurrentTemperature")]
    if vals:
        t = max(vals)
        det += f" | الحرارة {t:.0f}°C"
        if t >= 90:
            st, fix = BAD, "سخونة خطيرة: نظّف المراوح وغيّر المعجون الحراري."
        elif t >= 80:
            st, fix = WARN, "الحرارة عالية: نظّف المراوح وتأكد من التهوية."
    else:
        det += " | الحرارة: غير متاحة من هذا الجهاز"
    if (c.get("LoadPercentage") or 0) > 90 and st == OK:
        st, fix = WARN, "حمل المعالج عالٍ: افتح مدير المهام وشوف البرنامج المسبب."
    return [Result(G_PC, "المعالج", st, det, fix)]


def parse_battery_xml(text):
    root = ET.fromstring(text)
    data = {}
    for el in root.iter():
        tag = el.tag.split("}")[-1]
        if tag in ("DesignCapacity", "FullChargeCapacity", "CycleCount", "Chemistry") and tag not in data:
            data[tag] = (el.text or "").strip()
    return data


def check_battery():
    if not ps("Get-CimInstance Win32_Battery | Select-Object -First 1 -ExpandProperty Name"):
        return [Result(G_PC, "البطارية", INFO, "لا توجد بطارية (كمبيوتر مكتبي)")]
    path = os.path.join(tempfile.gettempdir(), "dd_battery.xml")
    if os.path.exists(path):
        os.remove(path)
    run(["powercfg", "/batteryreport", "/xml", "/output", path], 60)
    if not os.path.exists(path):
        return [Result(G_PC, "البطارية", WARN, "تعذّر إنشاء تقرير البطارية", "شغّل البرنامج كمسؤول.")]
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        d = parse_battery_xml(f.read())
    try:
        design, full = int(d["DesignCapacity"]), int(d["FullChargeCapacity"])
        pct = full * 100 / design
    except (KeyError, ValueError, ZeroDivisionError):
        return [Result(G_PC, "البطارية", INFO, "لا توجد قراءة للسعة من هذا الجهاز")]
    st = BAD if pct < 50 else WARN if pct < 75 else OK
    det = f"الصحة {pct:.0f}% (الأصلية {design} mWh، الحالية {full} mWh)"
    if d.get("CycleCount"):
        det += f" | دورات الشحن {d['CycleCount']}"
    fix = "البطارية ضعيفة؛ يُنصح باستبدالها." if st != OK else ""
    return [Result(G_PC, "البطارية", st, det, fix)]


PNP_CODES = {
    10: "الجهاز ما يقدر يبدأ", 22: "الجهاز معطّل", 28: "التعريف غير مثبت", 31: "الجهاز لا يعمل بشكل صحيح",
    39: "ملف التعريف تالف", 43: "ويندوز أوقف الجهاز لأنه أبلغ عن مشكلة (غالبًا عطل في القطعة)",
    45: "الجهاز غير متصل", 52: "التعريف غير موقّع",
}


def check_devices():
    rows = ps_json("Get-CimInstance Win32_PnPEntity | Where-Object { $_.ConfigManagerErrorCode -gt 0 } | "
                   "Select-Object Name,PNPClass,ConfigManagerErrorCode")
    if not rows:
        return [Result(G_PC, "مدير الأجهزة", OK, "لا توجد أجهزة بعلامة خطأ")]
    out = []
    for r in rows:
        code = r.get("ConfigManagerErrorCode")
        st = WARN if code in (22, 45) else BAD
        fix = {28: "ثبّت التعريف من موقع الشركة المصنّعة.", 22: "فعّل الجهاز من مدير الأجهزة إذا تحتاجه.",
               43: "حدّث التعريف؛ إذا استمرت المشكلة فالقطعة نفسها معطوبة."}.get(code, "حدّث التعريف أو افحص القطعة.")
        out.append(Result(G_PC, f"جهاز: {r.get('Name') or r.get('PNPClass') or '?'}", st,
                          f"كود الخطأ {code}: {PNP_CODES.get(code, 'خطأ غير معروف')}", fix))
    return out


def check_gpu():
    rows = ps_json("Get-CimInstance Win32_VideoController | Select-Object Name,DriverVersion,Status,ConfigManagerErrorCode")
    out = []
    for r in rows:
        bad = (r.get("Status") not in (None, "OK")) or (r.get("ConfigManagerErrorCode") or 0) > 0
        out.append(Result(G_PC, f"كرت الشاشة: {r.get('Name')}", BAD if bad else OK,
                          f"التعريف {r.get('DriverVersion')} | الحالة {r.get('Status')}",
                          "أعد تثبيت تعريف الكرت؛ إذا استمر العطل فالكرت معطوب." if bad else ""))
    return out or [Result(G_PC, "كرت الشاشة", WARN, "تعذّرت القراءة")]


HW_PROVIDERS = {
    "disk": "أخطاء في القرص", "Ntfs": "أخطاء في نظام الملفات", "stornvme": "أخطاء في قرص NVMe",
    "storahci": "أخطاء في متحكم التخزين", "Microsoft-Windows-WHEA-Logger": "أخطاء عتاد (CPU/رام/لوحة أم)",
    "Microsoft-Windows-Kernel-Power": "إطفاء مفاجئ (كهرباء/حرارة/باور)", "nvlddmkm": "انهيار تعريف NVIDIA",
    "amdkmdag": "انهيار تعريف AMD", "Display": "انهيار تعريف الشاشة",
}


def check_events():
    names = ",".join(f"'{p}'" for p in HW_PROVIDERS)
    rows = ps_json("$s=(Get-Date).AddDays(-30); Get-WinEvent -FilterHashtable @{LogName='System';Level=1,2,3;"
                   f"StartTime=$s;ProviderName={names}}} -ErrorAction SilentlyContinue | "
                   "Group-Object ProviderName | Select-Object Name,Count", 150)
    if not rows:
        return [Result(G_PC, "أخطاء العتاد (30 يوم)", OK, "ما فيه أخطاء قرص أو عتاد مسجلة")]
    out = []
    for r in rows:
        n = r.get("Count") or 0
        crit = r["Name"] in ("disk", "Ntfs", "stornvme", "storahci", "Microsoft-Windows-WHEA-Logger")
        st = (BAD if n >= 3 else WARN) if crit else WARN
        out.append(Result(G_PC, HW_PROVIDERS.get(r["Name"], r["Name"]), st, f"{n} حدث خلال آخر 30 يوم ({r['Name']})",
                          "افحص القرص/الرام وانسخ بياناتك." if crit else "راجع التعريفات والتبريد والباور."))
    return out


BUGCHECKS = {
    "0x0000000a": "IRQL_NOT_LESS_OR_EQUAL (تعريف أو رام)", "0x0000001a": "MEMORY_MANAGEMENT (غالبًا الرام)",
    "0x0000001e": "KMODE_EXCEPTION_NOT_HANDLED (تعريف)", "0x0000003b": "SYSTEM_SERVICE_EXCEPTION (تعريف)",
    "0x00000050": "PAGE_FAULT_IN_NONPAGED_AREA (رام أو قرص)", "0x0000007a": "KERNEL_DATA_INPAGE_ERROR (قرص)",
    "0x0000007b": "INACCESSIBLE_BOOT_DEVICE (قرص/متحكم)", "0x0000009f": "DRIVER_POWER_STATE_FAILURE (باور/تعريف)",
    "0x000000d1": "DRIVER_IRQL_NOT_LESS_OR_EQUAL (تعريف)", "0x000000f4": "CRITICAL_OBJECT_TERMINATION (قرص)",
    "0x00000116": "VIDEO_TDR_FAILURE (كرت الشاشة)", "0x00000117": "VIDEO_TDR_TIMEOUT (كرت الشاشة)",
    "0x00000124": "WHEA_UNCORRECTABLE_ERROR (عتاد: معالج/رام/باور)", "0x00000133": "DPC_WATCHDOG_VIOLATION (تعريف/قرص)",
    "0x00000019": "BAD_POOL_HEADER (رام أو تعريف)", "0x00000024": "NTFS_FILE_SYSTEM (قرص/نظام ملفات)",
    "0x00000101": "CLOCK_WATCHDOG_TIMEOUT (معالج)", "0x00000109": "CRITICAL_STRUCTURE_CORRUPTION (رام/تعريف)",
    "0x000000c2": "BAD_POOL_CALLER (تعريف/رام)", "0x000000ed": "UNMOUNTABLE_BOOT_VOLUME (قرص/نظام ملفات)",
    "0x000000ef": "CRITICAL_PROCESS_DIED (ملفات نظام أو قرص)", "0x0000009c": "MACHINE_CHECK_EXCEPTION (عتاد: معالج/حرارة)",
}


def bugcheck_hint(msg):
    m = re.search(r"0x[0-9a-fA-F]{8}", msg or "")
    if not m:
        return "غير معروف"
    code = "0x" + m.group(0)[2:].lower()
    return f"{code} {BUGCHECKS.get(code, '')}".strip()


def check_bsod():
    rows = ps_json("Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-WER-SystemErrorReporting';"
                   "Id=1001;StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | "
                   "Select-Object @{n='T';e={$_.TimeCreated.ToString('yyyy-MM-dd')}},Message", 100)
    if not rows:
        return [Result(G_PC, "الشاشة الزرقاء (30 يوم)", OK, "لا توجد شاشة زرقاء مسجلة")]
    det = " | ".join(f"{r.get('T')}: {bugcheck_hint(r.get('Message'))}" for r in rows[:4])
    return [Result(G_PC, "الشاشة الزرقاء (30 يوم)", BAD if len(rows) >= 3 else WARN,
                   f"{len(rows)} مرة — {det}",
                   "الكود يدل على السبب المحتمل (رام / قرص / تعريف / كرت شاشة). جرّب اختبار الرام وحدّث التعريفات.")]


def check_network():
    out = []
    code, _, _ = run(["ping", "-n" if IS_WIN else "-c", "2", "8.8.8.8"], 15)
    out.append(Result(G_PC, "الاتصال بالإنترنت", OK if code == 0 else WARN,
                      "الاتصال يعمل" if code == 0 else "لا يوجد اتصال بالإنترنت",
                      "" if code == 0 else "افحص الكيبل/الراوتر وكرت الشبكة."))
    try:
        socket.setdefaulttimeout(4)
        socket.gethostbyname("google.com")
        out.append(Result(G_PC, "DNS", OK, "تحليل الأسماء يعمل"))
    except OSError:
        out.append(Result(G_PC, "DNS", WARN, "فشل تحليل الأسماء", "غيّر DNS إلى 8.8.8.8 أو أعد تشغيل الراوتر."))
    return out


def check_system():
    cs = ps_json("Get-CimInstance Win32_ComputerSystem | Select-Object Manufacturer,Model")
    osr = ps_json("Get-CimInstance Win32_OperatingSystem | Select-Object Caption,Version")
    days = ps("[int](((Get-Date)-(Get-CimInstance Win32_OperatingSystem).LastBootUpTime).TotalDays)")
    out = []
    if cs and osr:
        out.append(Result(G_PC, "معلومات الجهاز", INFO,
                          f"{cs[0].get('Manufacturer')} {cs[0].get('Model')} | {osr[0].get('Caption')} ({osr[0].get('Version')})"))
    if days.isdigit():
        d = int(days)
        out.append(Result(G_PC, "مدة التشغيل", WARN if d > 14 else OK, f"{d} يوم منذ آخر إعادة تشغيل",
                          "أعد تشغيل الجهاز لتحديث النظام وتفريغ الذاكرة." if d > 14 else ""))
    if not is_admin():
        out.append(Result(G_PC, "صلاحيات البرنامج", WARN, "البرنامج يعمل بدون صلاحية مسؤول",
                          "أعد تشغيله بـ Run as administrator لقراءة SMART والحرارة بدقة."))
    return out


# ───────────────────────── اختبارات عميقة (Python صرف، تعمل بأي نظام) ─────────────────────────
def ram_test(mb=512):
    """يكتب 4 أنماط على mb ميغا من الذاكرة ويقرأها. يرجع (عدد الكتل التالفة، الميغا المفحوصة)."""
    chunk = 64 * 1024 * 1024
    blocks, remaining = [], mb * 1024 * 1024
    try:
        while remaining > 0:
            n = min(chunk, remaining)
            blocks.append(bytearray(n))
            remaining -= n
    except MemoryError:
        pass
    bad = 0
    for p in (0xAA, 0x55, 0xFF, 0x00):
        for b in blocks:
            pat = bytes([p]) * len(b)
            b[:] = pat
        for b in blocks:
            if b != bytes([p]) * len(b):
                bad += 1
    tested = sum(len(b) for b in blocks) // (1024 * 1024)
    del blocks
    return bad, tested


def cpu_stress(seconds=15, threads=None, sample=None):
    """يضغط كل الأنوية بـ SHA-256 (يحرر GIL). يرجع (ops/s إجمالي، نسبة التراجع آخر الفحص مقابل أوله، نتيجة sample())."""
    import hashlib
    n = threads or (os.cpu_count() or 2)
    seconds = max(3, int(seconds))
    buckets = [0] * (seconds + 1)
    t0 = time.time()
    end = t0 + seconds

    def work():
        buf = os.urandom(1 << 20)
        h = hashlib.sha256
        while True:
            now = time.time()
            if now >= end:
                break
            h(buf).digest()
            buckets[min(seconds, int(now - t0))] += 1

    ts = [threading.Thread(target=work, daemon=True) for _ in range(n)]
    for t in ts:
        t.start()
    sampled = None
    if sample:
        time.sleep(seconds * 0.7)
        try:
            sampled = sample()
        except Exception:
            sampled = None
    for t in ts:
        t.join()
    third = max(1, seconds // 3)
    first = sum(buckets[:third]) / third
    last = sum(buckets[seconds - third:seconds]) / third
    total = sum(buckets) / seconds
    return total, (last / first if first else 1.0), sampled


def disk_speed_test(folder, mb=256):
    """يكتب ملفًا عشوائيًا ثم يقرأه ويقارن CRC. يرجع dict(ok, wmbs, rmbs, err)."""
    path = os.path.join(folder, "dd_test.bin")
    blk = os.urandom(1 << 20)
    res = {"ok": False, "wmbs": 0.0, "rmbs": 0.0, "err": ""}
    try:
        os.makedirs(folder, exist_ok=True)
        crc_w = 0
        t0 = time.time()
        with open(path, "wb") as f:
            for _ in range(mb):
                f.write(blk)
                crc_w = zlib.crc32(blk, crc_w)
            f.flush()
            os.fsync(f.fileno())
        t1 = time.time()
        crc_r, tot = 0, 0
        with open(path, "rb") as f:
            while True:
                b = f.read(1 << 20)
                if not b:
                    break
                crc_r = zlib.crc32(b, crc_r)
                tot += len(b)
        t2 = time.time()
        res.update(ok=(crc_r == crc_w and tot == mb << 20), wmbs=mb / max(t1 - t0, 1e-3), rmbs=mb / max(t2 - t1, 1e-3))
    except OSError as e:
        res["err"] = str(e)
    finally:
        try:
            os.remove(path)
            os.rmdir(folder)
        except OSError:
            pass
    return res


def parse_ping(text):
    """يرجع (نسبة الفقد %, متوسط ms) من مخرجات ping ويندوز أو لينكس، أو (None, None)."""
    loss = re.search(r"\((\d+)%\s*(?:loss|packet loss)", text or "", re.I) or re.search(r"(\d+(?:\.\d+)?)%\s*packet loss", text or "", re.I)
    avg = re.search(r"Average\s*=\s*(\d+)\s*ms", text or "", re.I) or re.search(r"=\s*[\d.]+/([\d.]+)/", text or "")
    return (float(loss.group(1)) if loss else None), (float(avg.group(1)) if avg else None)


# ───────────────────────── فحوصات ويندوز المتقدمة ─────────────────────────
def acpi_temp():
    temps = ps_json("Get-CimInstance -Namespace root/wmi -ClassName MSAcpi_ThermalZoneTemperature "
                    "-ErrorAction SilentlyContinue | Select-Object CurrentTemperature")
    vals = [t["CurrentTemperature"] / 10 - 273.15 for t in temps if t.get("CurrentTemperature")]
    return max(vals) if vals else None


def check_inventory():
    out = []
    bb = ps_json("Get-CimInstance Win32_BaseBoard | Select-Object Manufacturer,Product")
    bios = ps_json("Get-CimInstance Win32_BIOS | Select-Object SMBIOSBIOSVersion,@{n='D';e={$_.ReleaseDate.ToString('yyyy-MM-dd')}}")
    cpu = ps_json("Get-CimInstance Win32_Processor | Select-Object Name,MaxClockSpeed,L3CacheSize")
    if bb or bios:
        b0, i0 = (bb or [{}])[0], (bios or [{}])[0]
        st, fix, age = INFO, "", ""
        try:
            y = int(str(i0.get("D", ""))[:4])
            age = f" ({datetime.date.today().year - y} سنة)"
            if datetime.date.today().year - y >= 8:
                st, fix = WARN, "البايوس قديم جدًا؛ فكّر بتحديثه (بحذر) إذا عندك مشاكل استقرار."
        except ValueError:
            pass
        out.append(Result(G_PC, "اللوحة الأم والبايوس", st,
                          f"{b0.get('Manufacturer', '?')} {b0.get('Product', '?')} | BIOS {i0.get('SMBIOSBIOSVersion', '?')} بتاريخ {i0.get('D', '?')}{age}", fix))
    if cpu:
        c = cpu[0]
        out.append(Result(G_PC, "مواصفات المعالج", INFO, f"{(c.get('Name') or '').strip()} | {c.get('MaxClockSpeed')}MHz | L3: {c.get('L3CacheSize')}KB"))
    mods = ps_json("Get-CimInstance Win32_PhysicalMemory | Select-Object Manufacturer,PartNumber,Capacity,Speed,ConfiguredClockSpeed")
    if mods:
        speeds = {m.get("ConfiguredClockSpeed") or m.get("Speed") for m in mods}
        st, fix = INFO, ""
        notes = []
        if len(speeds) > 1:
            st, fix = WARN, "شرائح الرام مختلفة السرعة فتشتغل كلها على أبطأ واحدة وقد تسبب عدم استقرار؛ الأفضل شرائح متطابقة."
        if len(mods) == 1:
            notes.append("قناة واحدة (Single channel) — أداء أقل")
        det = " ; ".join(f"{fmt_size(m.get('Capacity') or 0)} {m.get('Speed') or '?'}MHz {str(m.get('Manufacturer') or '').strip()} {str(m.get('PartNumber') or '').strip()}" for m in mods)
        out.append(Result(G_PC, "شرائح الرام", st, det + (" | " + " | ".join(notes) if notes else ""), fix))
    gpus = ps_json("Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM,DriverVersion")
    if gpus:
        out.append(Result(G_PC, "كروت الشاشة", INFO, " ; ".join(f"{g.get('Name')} (تعريف {g.get('DriverVersion')})" for g in gpus)))
    dd = ps_json("Get-CimInstance Win32_DiskDrive | Select-Object Model,Size,InterfaceType")
    if dd:
        out.append(Result(G_PC, "الأقراص (مواصفات)", INFO, " ; ".join(f"{d.get('Model')} {fmt_size(d.get('Size') or 0)} {d.get('InterfaceType')}" for d in dd)))
    return out


def check_reliability():
    out = []
    kp = ps_json("Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Microsoft-Windows-Kernel-Power';Id=41;"
                 "StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | Select-Object -First 50 TimeCreated", 100)
    n = len(kp)
    out.append(Result(G_PC, "الانطفاء المفاجئ (30 يوم)", BAD if n >= 3 else WARN if n else OK,
                      f"{n} مرة انطفى/أعاد التشغيل بدون إغلاق سليم" if n else "ما فيه انطفاء مفاجئ",
                      "الأسباب الشائعة: الباور سبلاي، سخونة، رام أو معالج؛ افحصهم بالترتيب." if n else ""))
    tdr = ps_json("Get-WinEvent -FilterHashtable @{LogName='System';ProviderName='Display';Id=4101;"
                  "StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | Select-Object -First 50 TimeCreated", 100)
    if tdr:
        out.append(Result(G_PC, "انهيار تعريف كرت الشاشة", WARN if len(tdr) < 5 else BAD, f"{len(tdr)} مرة خلال 30 يوم",
                          "أعد تثبيت تعريف كرت الشاشة؛ إذا استمر مع ألعاب/أحمال فالكرت أو التبريد فيه مشكلة."))
    crashes = ps_json("Get-WinEvent -FilterHashtable @{LogName='Application';ProviderName='Application Error';Id=1000;"
                      "StartTime=(Get-Date).AddDays(-30)} -ErrorAction SilentlyContinue | ForEach-Object { $_.Properties[0].Value } | "
                      "Group-Object | Sort-Object Count -Descending | Select-Object -First 5 Name,Count", 100)
    if crashes:
        tot = sum(c.get("Count") or 0 for c in crashes)
        out.append(Result(G_PC, "انهيار البرامج (30 يوم)", WARN if tot >= 10 else INFO,
                          " ; ".join(f"{c.get('Name')} ×{c.get('Count')}" for c in crashes),
                          "انهيارات كثيرة لبرامج مختلفة تدل غالبًا على رام أو قرص؛ لبرنامج واحد فقط: أعد تثبيته." if tot >= 10 else ""))
    return out


def check_security():
    out = []
    sb = ps("try { Confirm-SecureBootUEFI } catch { 'NA' }")
    out.append(Result(G_PC, "Secure Boot", INFO, {"True": "مفعّل", "False": "غير مفعّل", "NA": "غير مدعوم (BIOS قديم/Legacy)"}.get(sb.strip(), sb.strip() or "؟")))
    tpm = ps_json("Get-Tpm | Select-Object TpmPresent,TpmReady")
    if tpm:
        out.append(Result(G_PC, "TPM", INFO, f"موجود: {tpm[0].get('TpmPresent')} | جاهز: {tpm[0].get('TpmReady')}"))
    mp = ps_json("Get-MpComputerStatus -ErrorAction SilentlyContinue | Select-Object AntivirusEnabled,RealTimeProtectionEnabled,AntivirusSignatureAge")
    if mp:
        m = mp[0]
        off = not m.get("AntivirusEnabled") or not m.get("RealTimeProtectionEnabled")
        age = m.get("AntivirusSignatureAge") or 0
        out.append(Result(G_PC, "الحماية (Defender)", WARN if off or age > 7 else OK,
                          f"مفعّل: {m.get('AntivirusEnabled')} | حماية فورية: {m.get('RealTimeProtectionEnabled')} | عمر التعريفات: {age} يوم",
                          "فعّل الحماية أو حدّث التعريفات (إلا إذا عندك برنامج حماية ثاني)." if off or age > 7 else ""))
    hf = ps("(Get-HotFix | Sort-Object InstalledOn -Descending | Select-Object -First 1).InstalledOn.ToString('yyyy-MM-dd')")
    try:
        days = (datetime.date.today() - datetime.date.fromisoformat(hf.strip())).days
        out.append(Result(G_PC, "آخر تحديث ويندوز", WARN if days > 90 else OK, f"قبل {days} يوم ({hf.strip()})",
                          "الجهاز ما انحدّث من فترة؛ شغّل Windows Update." if days > 90 else ""))
    except ValueError:
        pass
    pend = ps("(Test-Path 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\WindowsUpdate\\Auto Update\\RebootRequired') -or "
              "(Test-Path 'HKLM:\\SYSTEM\\CurrentControlSet\\Control\\Session Manager\\PendingFileRenameOperations')")
    if pend.strip() == "True":
        out.append(Result(G_PC, "إعادة تشغيل معلقة", WARN, "ويندوز ينتظر إعادة تشغيل لإكمال تحديث/تثبيت", "أعد تشغيل الجهاز."))
    return out


def check_integrity():
    code, out, _ = run(["dism", "/online", "/cleanup-image", "/checkhealth"], 180)
    low = out.lower()
    if "no component store corruption" in low:
        return [Result(G_PC, "سلامة ملفات النظام (DISM)", OK, "لا يوجد تلف بملفات النظام")]
    if "repairable" in low or "corrupt" in low:
        return [Result(G_PC, "سلامة ملفات النظام (DISM)", BAD, "ملفات النظام فيها تلف قابل للإصلاح",
                       "افتح CMD كمسؤول: DISM /Online /Cleanup-Image /RestoreHealth ثم sfc /scannow.")]
    return [Result(G_PC, "سلامة ملفات النظام (DISM)", INFO,
                   "تعذّر التحقق" + ("" if is_admin() else " (يحتاج صلاحية مسؤول)"), "شغّل: sfc /scannow من CMD كمسؤول.")]


def check_network_quality():
    out = []
    gw = ps("(Get-NetRoute -DestinationPrefix 0.0.0.0/0 -ErrorAction SilentlyContinue | Sort-Object RouteMetric | Select-Object -First 1).NextHop").strip()
    for label, host in (("الراوتر", gw), ("الإنترنت 8.8.8.8", "8.8.8.8")):
        if not host:
            continue
        _, txt, _ = run(["ping", "-n" if IS_WIN else "-c", "10", host], 30)
        loss, avg = parse_ping(txt)
        if loss is None:
            out.append(Result(G_PC, f"جودة الاتصال: {label}", WARN, "ما وصلت ردود", "افحص الكيبل/الواي فاي."))
            continue
        st = BAD if loss >= 20 else WARN if loss >= 2 or (avg or 0) > 150 else OK
        out.append(Result(G_PC, f"جودة الاتصال: {label}", st, f"فقد حزم {loss:.0f}% | زمن الاستجابة {avg if avg is not None else '?'}ms",
                          "اتصال غير مستقر: جرّب كيبل آخر، قرّب من الراوتر، أو افحص كرت الشبكة." if st != OK else ""))
    nics = ps_json("Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up' | Select-Object Name,LinkSpeed")
    for n in nics:
        sp = str(n.get("LinkSpeed") or "")
        slow = bool(re.match(r"^(10|100) (M|K)bps", sp))
        out.append(Result(G_PC, f"كرت الشبكة {n.get('Name')}", WARN if slow else INFO, f"سرعة الربط {sp}",
                          "السرعة واطئة جدًا: غالبًا كيبل أو منفذ تالف." if slow else ""))
    return out


def check_services():
    rows = ps_json("Get-CimInstance Win32_Service -Filter \"StartMode='Auto' AND State='Stopped'\" | "
                   "Where-Object { $_.ExitCode -ne 0 -and $_.ExitCode -ne 1077 } | Select-Object -First 10 Name,ExitCode")
    if not rows:
        return [Result(G_PC, "خدمات ويندوز", OK, "ما فيه خدمات تلقائية فاشلة")]
    return [Result(G_PC, "خدمات ويندوز", WARN, "خدمات تلقائية متوقفة بخطأ: " + "، ".join(str(r.get("Name")) for r in rows),
                   "خدمات أساسية متوقفة تسبب أعطالًا؛ افتح services.msc وشغّلها وشوف السبب بسجل الأحداث.")]


# ───────────────────────── فحوصات عميقة (تستغرق دقائق) ─────────────────────────
def check_ram_test():
    os_ = ps_json("Get-CimInstance Win32_OperatingSystem | Select-Object FreePhysicalMemory")
    free_mb = (os_[0]["FreePhysicalMemory"] // 1024) if os_ else 1024
    mb = int(max(128, min(2048, free_mb * 0.5)))
    bad, tested = ram_test(mb)
    if bad:
        return [Result(G_PC, "اختبار الرام (عميق)", BAD, f"{bad} كتلة ذاكرة فيها أخطاء من أصل {tested}MB مفحوصة",
                       "الرام فيها عطل مؤكد؛ جرّب كل شريحة لوحدها واستبدل التالفة.")]
    return [Result(G_PC, "اختبار الرام (عميق)", OK, f"نجح فحص {tested}MB بـ4 أنماط بدون أخطاء",
                   "")]


def check_cpu_stress():
    t_before = acpi_temp()
    ops, ratio, sampled = cpu_stress(20, sample=lambda: ps("(Get-Counter '\\Processor Information(_Total)\\% Processor Performance').CounterSamples[0].CookedValue", 30))
    t_after = acpi_temp()
    st, fix, notes = OK, "", [f"أداء {ops:.0f} عملية/ث"]
    if t_after is not None:
        notes.append(f"الحرارة {t_before:.0f}→{t_after:.0f}°C" if t_before is not None else f"الحرارة بعد الضغط {t_after:.0f}°C")
        if t_after >= 95:
            st, fix = BAD, "حرارة خطيرة تحت الحمل: نظّف المراوح وبدّل المعجون الحراري."
        elif t_after >= 85:
            st, fix = WARN, "الحرارة عالية تحت الحمل: نظّف الغبار وحسّن التهوية."
    if ratio < 0.75:
        st = BAD if ratio < 0.5 else (WARN if st == OK else st)
        fix = fix or "الأداء نزل أثناء الاختبار (Throttling) بسبب الحرارة أو الباور."
        notes.append(f"تراجع الأداء {100 - ratio * 100:.0f}% خلال الفحص")
    try:
        f = float(sampled)
        notes.append(f"تردد المعالج تحت الحمل {f:.0f}% من الاسمي")
    except (TypeError, ValueError):
        pass
    return [Result(G_PC, "ضغط المعالج (20 ثانية)", st, " | ".join(notes), fix)]


def check_disk_speed():
    rows = ps_json("Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' | Select-Object DeviceID,FreeSpace")
    out = []
    for r in rows:
        if (r.get("FreeSpace") or 0) < 1 << 30:
            continue
        res = disk_speed_test(os.path.join(r["DeviceID"] + "\\", "DeviceDoctor_test"), 256)
        if res["err"]:
            out.append(Result(G_PC, f"سرعة وسلامة {r['DeviceID']}", WARN, f"تعذّر الاختبار: {res['err']}"))
            continue
        st = BAD if not res["ok"] else WARN if res["wmbs"] < 15 else OK
        out.append(Result(G_PC, f"سرعة وسلامة {r['DeviceID']}", st,
                          ("البيانات رجعت سليمة" if res["ok"] else "البيانات رجعت تالفة!") +
                          f" | كتابة {res['wmbs']:.0f} MB/s | قراءة {res['rmbs']:.0f} MB/s (القراءة قد تتأثر بالكاش)",
                          "انسخ بياناتك فورًا؛ القرص يخرب البيانات." if not res["ok"] else "الكتابة بطيئة جدًا: قرص قديم/ممتلئ أو فيه عطل." if st == WARN else ""))
    return out


def pc_checks(deep=False):
    base = [("الأقراص", check_disks), ("مساحة التخزين", check_space), ("الذاكرة", check_ram),
            ("المعالج", check_cpu), ("البطارية", check_battery), ("الأجهزة المعطلة", check_devices),
            ("كرت الشاشة", check_gpu), ("أخطاء العتاد", check_events), ("الشاشة الزرقاء", check_bsod),
            ("الاعتمادية والانطفاء المفاجئ", check_reliability), ("الشبكة", check_network),
            ("جودة الشبكة", check_network_quality), ("الخدمات", check_services), ("الأمان والتحديثات", check_security),
            ("المواصفات", check_inventory), ("معلومات النظام", check_system)]
    if deep:
        base += [("سلامة ملفات النظام", check_integrity), ("سرعة وسلامة الأقراص", check_disk_speed),
                 ("اختبار الرام العميق", check_ram_test), ("ضغط المعالج", check_cpu_stress)]
    return base


# ───────────────────────── ويندوز على قرص معطّل (قرص موصول بكمبيوتر سليم) ─────────────────────────
G_OFF = "قرص معطّل"
def ci_join(base, *parts):
    cur = base
    for part in parts:
        try:
            names = os.listdir(cur)
        except OSError:
            return None
        m = [n for n in names if n.lower() == part.lower()]
        if not m:
            return None
        cur = os.path.join(cur, m[0])
    return cur


def read_bugcheck(path):
    try:
        with open(path, "rb") as f:
            h = f.read(0x40)
    except OSError:
        return None
    if h[:8] == b"PAGEDU64" and len(h) >= 0x3C:
        return struct.unpack_from("<I", h, 0x38)[0]
    if h[:8] == b"PAGEDUMP" and len(h) >= 0x2C:
        return struct.unpack_from("<I", h, 0x28)[0]
    return None


def bugcheck_text(code):
    key = "0x%08x" % code
    return f"{key} {BUGCHECKS.get(key, '')}".strip()


def decode_text(raw):
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff") or b"\x00" in raw[:200]:
        return raw.decode("utf-16", "replace")
    return raw.decode("cp1252", "replace")


def inspect_windows(root):
    """root: جذر قسم NTFS (مُركَّب أو حرف قرص). يرجع None إذا ما فيه ويندوز."""
    if not ci_join(root, "Windows", "System32", "config", "SYSTEM"):
        return None
    info = {"missing": [], "dumps": [], "srt": "", "free_pct": None}
    need = [("Windows", "System32", "ntoskrnl.exe"), ("Windows", "System32", "config", "SOFTWARE")]
    for rel in need:
        p = ci_join(root, *rel)
        if not p or os.path.getsize(p) == 0:
            info["missing"].append("\\".join(rel))
    if not (ci_join(root, "Windows", "System32", "winload.exe") or ci_join(root, "Windows", "System32", "winload.efi")):
        info["missing"].append("Windows\\System32\\winload.exe/.efi")
    d = ci_join(root, "Windows", "Minidump")
    files = []
    if d:
        files = sorted(glob.glob(os.path.join(d, "*")), key=os.path.getmtime, reverse=True)
    full = ci_join(root, "Windows", "MEMORY.DMP")
    if full:
        files.insert(0, full)
    for p in files[:6]:
        code = read_bugcheck(p)
        if code is not None:
            when = datetime.datetime.fromtimestamp(os.path.getmtime(p)).strftime("%Y-%m-%d")
            info["dumps"].append(f"{when}: {bugcheck_text(code)}")
    srt = ci_join(root, "Windows", "System32", "LogFiles", "Srt", "SrtTrail.txt")
    if srt:
        try:
            with open(srt, "rb") as f:
                txt = decode_text(f.read(20000))
            m = re.search(r"Root cause found:(.{0,500})", txt, re.S)
            info["srt"] = " ".join((m.group(1) if m else txt[-400:]).split())[:400]
        except OSError:
            pass
    try:
        du = shutil.disk_usage(root)
        info["free_pct"] = du.free * 100 / du.total if du.total else None
    except OSError:
        pass
    return info




def windows_info_to_result(group, name, info):
    """يحوّل نتيجة inspect_windows إلى Result واحد (دالة نقية قابلة للاختبار)."""
    st, notes, fixes = OK, [], []

    def up(x):
        nonlocal st
        if _ORDER[x] > _ORDER[st]:
            st = x
    if info["missing"]:
        up(BAD)
        notes.append("ملفات نظام ناقصة/فارغة: " + ", ".join(info["missing"]))
        fixes.append("ويندوز تالف: جرّب Startup Repair من قرص التثبيت أو إعادة تثبيت/إصلاح.")
    if info["free_pct"] is not None:
        notes.append(f"المساحة الحرة {info['free_pct']:.0f}%")
        if info["free_pct"] < 5:
            up(BAD)
            fixes.append("القسم ممتلئ تقريبًا وهذا قد يمنع الإقلاع: احذف/انقل ملفات.")
        elif info["free_pct"] < 10:
            up(WARN)
    if info["dumps"]:
        up(BAD if len(info["dumps"]) >= 3 else WARN)
        notes.append("شاشات زرقاء: " + " ؛ ".join(info["dumps"]))
        fixes.append("الكود يدل على السبب (رام/قرص/تعريف/كرت شاشة/معالج)؛ اختبر القطعة المذكورة.")
    if info["srt"]:
        up(WARN)
        notes.append("سجل Startup Repair: " + info["srt"])
    return Result(group, name, st, " | ".join(notes) or "الملفات الأساسية موجودة", " ".join(fixes))


def find_offline_windows():
    """يرجع جذور الأقراص الموصولة (غير قرص النظام الحالي) اللي فيها نسخة ويندوز."""
    sysdrive = os.environ.get("SystemDrive", "C:").upper()
    out = []
    for l in string.ascii_uppercase:
        root = f"{l}:\\"
        if f"{l}:" == sysdrive or not os.path.exists(root):
            continue
        if ci_join(root, "Windows", "System32", "config", "SYSTEM"):
            out.append(root)
    return out


def _evtx_ps(path, xpath, take, sel, timeout=180):
    p = path.replace("'", "''")
    return ps_json(f"Get-WinEvent -Path '{p}' -FilterXPath \"{xpath}\" -MaxEvents {take} -ErrorAction SilentlyContinue | {sel}", timeout)


def check_offline_events(root):
    evtx = ci_join(root, "Windows", "System32", "winevt", "Logs", "System.evtx")
    if not evtx:
        return [Result(G_OFF, "سجل الأحداث", WARN, "ملف System.evtx غير موجود/غير مقروء")]
    out = []
    last = _evtx_ps(evtx, "*", 1, "Select-Object @{n='T';e={$_.TimeCreated.ToString('yyyy-MM-dd HH:mm')}}", 90)
    if last:
        out.append(Result(G_OFF, "آخر نشاط مسجّل بالجهاز", INFO, f"{last[0].get('T')} — هذا غالبًا وقت آخر مرة اشتغل فيها أو انطفى",
                          "قارنه بوقت ظهور العطل لتعرف هل مات فجأة أم تدريجيًا."))
    kp = _evtx_ps(evtx, "*[System[Provider[@Name='Microsoft-Windows-Kernel-Power'] and (EventID=41)]]", 200, "Select-Object TimeCreated")
    if kp:
        out.append(Result(G_OFF, "الانطفاء المفاجئ (Kernel-Power 41)", BAD if len(kp) >= 3 else WARN, f"{len(kp)} مرة مسجلة",
                          "الأسباب الشائعة: الباور سبلاي، سخونة، رام أو معالج."))
    bc = _evtx_ps(evtx, "*[System[Provider[@Name='Microsoft-Windows-WER-SystemErrorReporting'] and (EventID=1001)]]", 5,
                  "Select-Object @{n='T';e={$_.TimeCreated.ToString('yyyy-MM-dd')}},Message", 90)
    if bc:
        out.append(Result(G_OFF, "الشاشة الزرقاء (من السجل)", BAD if len(bc) >= 3 else WARN,
                          " | ".join(f"{r.get('T')}: {bugcheck_hint(r.get('Message'))}" for r in bc),
                          "الكود يدل على السبب (رام / قرص / تعريف / كرت شاشة / معالج)."))
    errs = _evtx_ps(evtx, "*[System[(Level=1 or Level=2)]]", 3000, "Group-Object ProviderName | Sort-Object Count -Descending | Select-Object -First 8 Name,Count")
    crit = {"disk", "Ntfs", "stornvme", "storahci", "Microsoft-Windows-WHEA-Logger", "volmgr", "iaStorA", "storport"}
    for e in errs:
        n = e.get("Count") or 0
        if e.get("Name") in crit and n >= 3:
            out.append(Result(G_OFF, f"أخطاء عتاد: {HW_PROVIDERS.get(e['Name'], e['Name'])}", BAD if n >= 10 else WARN,
                              f"{n} خطأ مسجّل ({e['Name']})", "افحص القرص/الرام/الباور؛ انسخ بياناتك فورًا إذا الأخطاء من القرص."))
    if errs:
        out.append(Result(G_OFF, "أكثر مصادر الأخطاء", INFO, " ; ".join(f"{e.get('Name')} ×{e.get('Count')}" for e in errs)))
    return out


def check_offline_disk(letter):
    script = (f"Get-Partition -DriveLetter {letter} | Get-Disk | Get-PhysicalDisk | ForEach-Object {{ "
              "$r = $_ | Get-StorageReliabilityCounter -ErrorAction SilentlyContinue; "
              "[pscustomobject]@{Name=$_.FriendlyName; Media=[string]$_.MediaType; Health=[string]$_.HealthStatus; "
              "Size=$_.Size; Temp=$r.Temperature; Wear=$r.Wear; Hours=$r.PowerOnHours; "
              "ReadErr=$r.ReadErrorsUncorrected; WriteErr=$r.WriteErrorsUncorrected} }")
    disks = ps_json(script)
    if not disks:
        return [Result(G_OFF, "صحة القرص (SMART)", WARN, "تعذّرت قراءة SMART (وصلات USB كثيرًا ما تمنعها)",
                       "جرّب توصيل القرص بمنفذ SATA مباشر أو علبة USB أفضل، أو شغّل CrystalDiskInfo.")]
    out = []
    for d in disks:
        st, det, fix = analyze_disk(d)
        out.append(Result(G_OFF, f"صحة القرص: {d.get('Name') or '?'}", st, det, fix))
    return out


def check_offline_fs(letter, deep=False):
    out = []
    _, txt, _ = run(["fsutil", "dirty", "query", f"{letter}:"], 20)
    if re.search(r"NOT\s+Dirty", txt, re.I):
        out.append(Result(G_OFF, "حالة نظام الملفات", OK, "القسم نظيف (Dirty bit غير مفعّل)"))
    elif re.search(r"Dirty", txt, re.I):
        out.append(Result(G_OFF, "حالة نظام الملفات", WARN, "القسم غير نظيف (Dirty): انطفاء مفاجئ أو أخطاء قرص",
                          f"شغّل chkdsk {letter}: /f من كمبيوتر سليم بعد ما تنسخ بياناتك."))
    if deep:
        code, txt, _ = run(["chkdsk", f"{letter}:", "/scan"], 900)
        if code == 0:
            out.append(Result(G_OFF, "فحص نظام الملفات (chkdsk)", OK, "ما لقى أخطاء"))
        elif code in (1, 2, 3):
            out.append(Result(G_OFF, "فحص نظام الملفات (chkdsk)", BAD, "لقى أخطاء بنظام الملفات",
                              f"انسخ بياناتك ثم chkdsk {letter}: /f /r (قد ياخذ ساعات إذا القرص متضرر)."))
        else:
            out.append(Result(G_OFF, "فحص نظام الملفات (chkdsk)", INFO, "تعذّر إكمال الفحص", ""))
    return out


def check_offline_windows(root, deep=False):
    letter = root[0].upper()
    out = []
    info = inspect_windows(root)
    if info:
        out.append(windows_info_to_result(G_OFF, f"ويندوز على {letter}:", info))
    else:
        out.append(Result(G_OFF, f"ويندوز على {letter}:", WARN, "ما لقيت نسخة ويندوز سليمة على هذا القسم"))
    for label, fn in (("SMART", lambda: check_offline_disk(letter)), ("نظام الملفات", lambda: check_offline_fs(letter, deep)),
                      ("سجل الأحداث", lambda: check_offline_events(root))):
        out += safe(G_OFF, label, fn)
    return out


# ───────────────────────── كيبل مباشر بين كمبيوترين ─────────────────────────
DIRECT_ME, DIRECT_PEER = "192.168.77.1", "192.168.77.2"


def list_adapters():
    return ps_json("Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object Status -eq 'Up' | Select-Object Name,InterfaceDescription,LinkSpeed")


def set_static_ip(name, ip=DIRECT_ME, mask="255.255.255.0"):
    code, _, _ = run(["netsh", "interface", "ip", "set", "address", f"name={name}", "static", ip, mask], 30)
    return code == 0


def restore_dhcp(name):
    code, _, _ = run(["netsh", "interface", "ip", "set", "address", f"name={name}", "source=dhcp"], 30)
    return code == 0


# ───────────────────────── فحص الموبايل (ADB) ─────────────────────────
G_PH = "الموبايل"


def find_adb():
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    for name in ("adb.exe", "adb"):
        p = os.path.join(base, "platform-tools", name)
        if os.path.exists(p):
            return p
    return shutil.which("adb")


class Adb:
    def __init__(self, exe, serial):
        self.exe, self.serial = exe, serial

    def sh(self, cmd, timeout=40):
        return run([self.exe, "-s", self.serial, "shell", cmd], timeout)[1]

    def prop(self, key):
        return self.sh(f"getprop {key}").strip()


USB_STEPS = ("1) إعدادات ← حول الهاتف ← اضغط 7 مرات على «رقم الإصدار» لتفعيل خيارات المطور. "
             "2) خيارات المطور ← فعّل «USB debugging». 3) وصّل الكابل (لازم كابل بيانات مو شحن فقط) ووافق على نافذة «السماح بالتصحيح» في الموبايل.")


def parse_devices(text):
    devs = []
    for line in text.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devs.append((parts[0], parts[1]))
    return devs


def phone_prepare():
    exe = find_adb()
    if not exe:
        return None, [Result(G_PH, "ADB", BAD, "ملف adb غير موجود", "ثبّت Android platform-tools أو استخدم النسخة المرفقة بالبرنامج.")]
    run([exe, "start-server"], 20)
    devs = parse_devices(run([exe, "devices"], 20)[1])
    ready = [d for d in devs if d[1] == "device"]
    if ready:
        res = []
        if len(ready) > 1:
            res.append(Result(G_PH, "عدة أجهزة", INFO, f"متصل {len(ready)} أجهزة؛ سيُفحص الأول فقط"))
        return Adb(exe, ready[0][0]), res
    if any(d[1] == "unauthorized" for d in devs):
        return None, [Result(G_PH, "الاتصال", WARN, "الموبايل متصل لكنه لم يسمح بالتصحيح",
                             "افتح الموبايل ووافق على نافذة «السماح بتصحيح USB» (فعّل «السماح دائمًا»). " + USB_STEPS)]
    return None, [Result(G_PH, "الاتصال", WARN, "لم يتم العثور على موبايل متصل", USB_STEPS)]


def parse_kv(text):
    d = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z][A-Za-z _]*?):\s*(.+?)\s*$", line)
        if m:
            d.setdefault(m.group(1).strip().lower(), m.group(2))
    return d


def parse_sensor_types(text):
    return set(re.findall(r"android\.sensor\.([a-z_\.]+)", text))


def parse_meminfo(text):
    d = {}
    for m in re.finditer(r"^(\w+):\s+(\d+)\s*kB", text, re.M):
        d[m.group(1)] = int(m.group(2)) * 1024
    return d


def parse_df(text):
    for line in reversed(text.strip().splitlines()):
        m = re.search(r"(\d+)%", line)
        parts = line.split()
        if m and len(parts) >= 4:
            return int(m.group(1)), parts
    return None, []


def parse_crashes(text):
    procs = re.findall(r"Process:\s*([\w\.]+),\s*PID", text)
    return text.count("FATAL EXCEPTION"), procs, len(re.findall(r"ANR in ", text))


HEALTH = {1: "غير معروفة", 2: "جيدة", 3: "سخونة زائدة", 4: "تالفة", 5: "فولتية زائدة", 6: "عطل غير محدد", 7: "باردة جدًا"}


def ph_info(adb):
    g = lambda k: adb.prop(k)
    det = f"{g('ro.product.manufacturer')} {g('ro.product.model')} | أندرويد {g('ro.build.version.release')} | الباتش الأمني {g('ro.build.version.security_patch') or '?'}"
    up = adb.sh("cat /proc/uptime").split()
    if up:
        try:
            det += f" | يعمل منذ {float(up[0]) / 86400:.1f} يوم"
        except ValueError:
            pass
    return [Result(G_PH, "معلومات الجهاز", INFO, det)]


def ph_battery(adb):
    kv = parse_kv(adb.sh("dumpsys battery"))
    if not kv:
        return [Result(G_PH, "البطارية", WARN, "تعذّرت قراءة البطارية")]
    out = []
    try:
        health = int(kv.get("health", "1"))
        temp = int(kv.get("temperature", "0")) / 10
        level = kv.get("level", "?")
        volt = kv.get("voltage", "?")
    except ValueError:
        return [Result(G_PH, "البطارية", WARN, "قراءة غير صالحة")]
    st = OK
    fix = ""
    if health in (3, 4, 5, 6):
        st, fix = BAD, "البطارية معطوبة أو تسخن؛ افصل الشاحن واستبدلها."
    elif health in (1, 7):
        st = WARN
    if temp >= 45:
        st, fix = BAD, "الحرارة عالية جدًا؛ افصل الشاحن وأبعد الجهاز عن الحرارة."
    elif temp >= 40 and st == OK:
        st, fix = WARN, "الحرارة مرتفعة نسبيًا."
    if kv.get("present", "true") == "false":
        st, fix = BAD, "النظام ما يشوف البطارية (متفككة أو معطوبة)."
    out.append(Result(G_PH, "البطارية", st,
                      f"الشحن {level}% | الحالة: {HEALTH.get(health, health)} | الحرارة {temp:.1f}°C | الفولتية {volt} mV | النوع {kv.get('technology', '?')}", fix))
    cyc = adb.sh("cat /sys/class/power_supply/battery/cycle_count 2>/dev/null").strip()
    full = adb.sh("cat /sys/class/power_supply/battery/charge_full 2>/dev/null").strip()
    des = adb.sh("cat /sys/class/power_supply/battery/charge_full_design 2>/dev/null").strip()
    notes, st2, fix2 = [], OK, ""
    if cyc.isdigit():
        notes.append(f"دورات الشحن {cyc}")
        if int(cyc) > 800:
            st2, fix2 = BAD, "عدد دورات كبير؛ البطارية على الأغلب متدهورة."
        elif int(cyc) > 500:
            st2, fix2 = WARN, "البطارية قاربت على نهاية عمرها."
    if full.isdigit() and des.isdigit() and int(des) > 0:
        pct = int(full) * 100 / int(des)
        notes.append(f"السعة الحالية {pct:.0f}% من الأصلية")
        if pct < 60:
            st2, fix2 = BAD, "سعة البطارية ضعيفة جدًا؛ استبدلها."
        elif pct < 80 and st2 == OK:
            st2, fix2 = WARN, "سعة البطارية متراجعة."
    if notes:
        out.append(Result(G_PH, "عمر البطارية", st2, " | ".join(notes), fix2))
    else:
        out.append(Result(G_PH, "عمر البطارية", INFO, "هذا الجهاز لا يكشف سعة/دورات البطارية عبر ADB",
                          "استخدم تطبيق الموبايل (APK) أو قائمة الخدمة *#*#4636#*#* حسب الجهاز."))
    return out


SENSORS_REQ = {"accelerometer": ("مقياس التسارع", BAD), "gyroscope": ("الجيروسكوب", WARN),
               "proximity": ("حساس القرب", WARN), "light": ("حساس الإضاءة", WARN),
               "magnetic_field": ("البوصلة", WARN)}


def ph_sensors(adb):
    types = parse_sensor_types(adb.sh("dumpsys sensorservice", 60))
    if not types:
        return [Result(G_PH, "الحساسات", INFO, "تعذّرت قراءة قائمة الحساسات")]
    out = []
    missing = [k for k in SENSORS_REQ if k not in types]
    if not missing:
        out.append(Result(G_PH, "الحساسات الأساسية", OK, f"كلها موجودة ({len(types)} نوع حساس)"))
    for k in missing:
        name, st = SENSORS_REQ[k]
        out.append(Result(G_PH, f"حساس مفقود: {name}", st, "غير ظاهر للنظام",
                          "إذا الموديل يفترض فيه هذا الحساس فهو معطّل (سلك/لوحة)؛ بعض الموبايلات الاقتصادية ما فيها."))
    return out


def ph_storage(adb):
    out = []
    pct, parts = parse_df(adb.sh("df /data"))
    if pct is not None:
        st = BAD if pct >= 95 else WARN if pct >= 85 else OK
        out.append(Result(G_PH, "التخزين", st, f"مستخدم {pct}%", "امسح ملفات/تطبيقات غير مهمة." if st != OK else ""))
    mem = parse_meminfo(adb.sh("cat /proc/meminfo"))
    if mem.get("MemTotal"):
        avail = mem.get("MemAvailable", 0)
        used = 100 - avail * 100 / mem["MemTotal"]
        st = WARN if used > 90 else OK
        out.append(Result(G_PH, "الذاكرة RAM", st, f"الإجمالي {fmt_size(mem['MemTotal'])} | مستخدم {used:.0f}%",
                          "أغلق التطبيقات الثقيلة." if st != OK else ""))
    return out


def ph_thermal(adb):
    m = re.search(r"Thermal Status:\s*(\d+)", adb.sh("dumpsys thermalservice"))
    if not m:
        return []
    s = int(m.group(1))
    st = BAD if s >= 3 else WARN if s == 2 else OK
    return [Result(G_PH, "الحرارة العامة", st, f"مستوى الحرارة {s} (0 طبيعي … 6 طوارئ)",
                   "الجهاز يسخن: أغلق التطبيقات وأخرج الغطاء." if st != OK else "")]


def ph_crashes(adb):
    text = adb.sh("logcat -d -b crash -t 1500", 60) + "\n" + adb.sh("logcat -d -b main -t 4000 *:E", 60)
    n, procs, anr = parse_crashes(text)
    out = []
    if n == 0 and anr == 0:
        out.append(Result(G_PH, "انهيار التطبيقات", OK, "لا انهيارات حديثة في السجل"))
    else:
        top = {}
        for p in procs:
            top[p] = top.get(p, 0) + 1
        topn = ", ".join(f"{k} ({v})" for k, v in sorted(top.items(), key=lambda x: -x[1])[:4]) or "—"
        out.append(Result(G_PH, "انهيار التطبيقات", WARN if n < 10 else BAD,
                          f"{n} انهيار و{anr} تجمّد (ANR) | الأكثر: {topn}",
                          "حدّث التطبيقات المسببة أو أعد تثبيتها أو احذفها."))
    reason = (adb.prop("sys.boot.reason") or adb.prop("ro.boot.bootreason")).lower()
    if "panic" in reason or "watchdog" in reason:
        out.append(Result(G_PH, "سبب آخر إقلاع", BAD, f"آخر إقلاع بعد: {reason}",
                          "انهيار نظام: غالبًا برنامج النظام أو الرام/اللوحة؛ جرّب تصفير المصنع ثم افحص الصيانة."))
    elif reason:
        out.append(Result(G_PH, "سبب آخر إقلاع", INFO, reason))
    return out


def ph_connectivity(adb):
    out = []
    sim = adb.prop("gsm.sim.state")
    if sim:
        absent = "ABSENT" in sim.upper() and "READY" not in sim.upper()
        out.append(Result(G_PH, "الشريحة SIM", WARN if absent else OK, f"الحالة: {sim}",
                          "أدخل الشريحة أو نظّف التماس/الدرج." if absent else ""))
    wifi = adb.sh("cmd wifi status 2>/dev/null").lower()
    if wifi:
        on = "enabled" in wifi
        out.append(Result(G_PH, "الواي فاي", OK if on else INFO, "مفعّل" if on else "مطفأ"))
    return out


def ph_security(adb):
    out = []
    p = adb.prop("ro.build.version.security_patch")
    try:
        age = (datetime.date.today() - datetime.date.fromisoformat(p)).days
        out.append(Result(G_PH, "تحديث الأمان", WARN if age > 365 else OK, f"آخر باتش أمني {p} (قبل {age} يوم)",
                          "الجهاز قديم أمنيًا؛ حدّث النظام إن توفر." if age > 365 else ""))
    except ValueError:
        pass
    vb = adb.prop("ro.boot.verifiedbootstate")
    if vb in ("orange", "yellow", "red"):
        out.append(Result(G_PH, "حالة الإقلاع", INFO, f"verified boot = {vb} (بوتلودر مفتوح أو نظام معدّل)"))
    if "test-keys" in adb.prop("ro.build.tags"):
        out.append(Result(G_PH, "نظام مخصص", INFO, "الجهاز يشغّل نظامًا غير رسمي (test-keys)"))
    return out


def phone_checks(adb):
    return [("معلومات الجهاز", lambda: ph_info(adb)), ("البطارية", lambda: ph_battery(adb)),
            ("الحساسات", lambda: ph_sensors(adb)), ("التخزين والرام", lambda: ph_storage(adb)),
            ("الحرارة", lambda: ph_thermal(adb)), ("الانهيارات", lambda: ph_crashes(adb)),
            ("الاتصال", lambda: ph_connectivity(adb)), ("الأمان", lambda: ph_security(adb))]


# ───────────────────────── التقرير ─────────────────────────
LABEL = {OK: "سليم", WARN: "تحذير", BAD: "عطل", INFO: "معلومة"}
COLOR = {OK: "#e6f4ea", WARN: "#fff4ce", BAD: "#fde7e9", INFO: "#eef3fb"}


def make_html(results, title="تقرير فحص الأجهزة"):
    cnt = {k: sum(1 for r in results if r.status == k) for k in LABEL}
    rows = "".join(
        f'<tr style="background:{COLOR[r.status]}"><td>{LABEL[r.status]}</td><td>{html.escape(r.group)}</td>'
        f'<td>{html.escape(r.name)}</td><td>{html.escape(r.details)}</td><td>{html.escape(r.fix)}</td></tr>'
        for r in sorted(results, key=lambda r: -_ORDER[r.status]))
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    return (f'<!doctype html><html dir="rtl" lang="ar"><meta charset="utf-8"><title>{title}</title>'
            '<style>body{font-family:Segoe UI,Tahoma,sans-serif;margin:24px}table{border-collapse:collapse;width:100%}'
            'td,th{border:1px solid #ccc;padding:8px;text-align:right;vertical-align:top}th{background:#222;color:#fff}</style>'
            f'<h2>{title}</h2><p>{now} — سليم {cnt[OK]} | تحذير {cnt[WARN]} | عطل {cnt[BAD]}</p>'
            '<table><tr><th>الحالة</th><th>القسم</th><th>الفحص</th><th>التفاصيل</th><th>الحل المقترح</th></tr>'
            f'{rows}</table></html>')


# ───────────────────────── وضع الاستقبال (فحص عن بعد عبر الواي فاي) ─────────────────────────
def local_ip():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sk:
            sk.connect(("8.8.8.8", 80))  # لا يرسل شيئًا، فقط لمعرفة الواجهة
            return sk.getsockname()[0]
    except OSError:
        try:
            return socket.gethostbyname(socket.gethostname())
        except OSError:
            return "127.0.0.1"


def run_all_pc(deep=False):
    if not IS_WIN:
        return [Result(G_PC, "فحص الكمبيوتر", INFO, "فحص الكمبيوتر يعمل على ويندوز فقط")]
    out = []
    for label, fn in pc_checks(deep):
        out += safe(G_PC, label, fn)
    return out


def results_to_json(results):
    return [{"group": r.group, "name": r.name, "status": r.status, "details": r.details, "fix": r.fix} for r in results]


# ───────────────────────── وضع الاستقبال + الفحص عن بعد ─────────────────────────
FW_RULE = "DeviceDoctor Receive"


def firewall_open(port, add=True):
    """يفتح/يغلق قاعدة جدار ناري داخلة للمنفذ (يحتاج مسؤول؛ يفشل بصمت)."""
    if not IS_WIN:
        return False
    if add:
        run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FW_RULE}"], 15)
        code, _, _ = run(["netsh", "advfirewall", "firewall", "add", "rule", f"name={FW_RULE}", "dir=in", "action=allow",
                          "protocol=TCP", f"localport={port}", "profile=private,domain"], 15)
        return code == 0
    run(["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FW_RULE}"], 15)
    return True


class RemoteServer:
    """سيرفر HTTP صغير: /ping و /scan?pin=XXXX[&deep=1]، يستخدمه تطبيق الموبايل أو EXE آخر."""
    MAX_BAD, LOCK_SECS = 5, 60

    def __init__(self, port=8765, scan_fn=None):
        self.port = port
        self.pin = f"{secrets.randbelow(10000):04d}"
        self.httpd = None
        self.lock = threading.Lock()
        self.scan_fn = scan_fn or run_all_pc
        self.bad, self.locked_until = 0, 0.0
        self.fw = False

    def _pin_ok(self, given):
        now = time.time()
        if now < self.locked_until:
            return None
        if secrets.compare_digest(given, self.pin):
            self.bad = 0
            return True
        self.bad += 1
        if self.bad >= self.MAX_BAD:
            self.bad, self.locked_until = 0, now + self.LOCK_SECS
        return False

    def start(self):
        outer = self

        class H(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, obj):
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                u = urllib.parse.urlparse(self.path)
                q = urllib.parse.parse_qs(u.query)
                name = socket.gethostname()
                if u.path == "/ping":
                    return self._send(200, {"type": "pc", "name": name})
                if u.path == "/scan":
                    ok = outer._pin_ok(q.get("pin", [""])[0])
                    if ok is None:
                        return self._send(429, {"error": "locked"})
                    if not ok:
                        return self._send(403, {"error": "bad pin"})
                    if not outer.lock.acquire(blocking=False):
                        return self._send(429, {"error": "busy"})
                    try:
                        deep = q.get("deep", ["0"])[0] == "1"
                        try:
                            res = outer.scan_fn(deep)
                        except TypeError:
                            res = outer.scan_fn()
                    finally:
                        outer.lock.release()
                    return self._send(200, {"type": "pc", "name": name, "results": results_to_json(res)})
                self._send(404, {"error": "not found"})

        self.httpd = http.server.ThreadingHTTPServer(("0.0.0.0", self.port), H)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.fw = firewall_open(self.port, True)

    def stop(self):
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
            self.httpd = None
        if self.fw:
            firewall_open(self.port, False)
            self.fw = False


def _http_json(url, timeout):
    with urllib.request.urlopen(url, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def remote_ping(ip, port=8765, timeout=3):
    try:
        return _http_json(f"http://{ip}:{port}/ping", timeout)
    except Exception:
        return None


def phone_report_results(d, name="موبايل"):
    """يحوّل تقرير تطبيق الموبايل (/report) إلى نتائج Result."""
    g = f"[{name}]"
    out = []
    b = d.get("battery") or {}
    if b.get("available"):
        health = {2: "جيدة", 3: "سخونة زائدة", 4: "تالفة", 5: "فولتية زائدة", 6: "عطل غير محدد", 7: "باردة جدًا"}.get(b.get("health"), "غير معروفة")
        st = BAD if b.get("health") in (3, 4, 5, 6) or b.get("temperature", 0) >= 45 else WARN if b.get("temperature", 0) >= 40 else OK
        out.append(Result(g, "البطارية", st, f"الشحن {b.get('level')}% | الحالة: {health} | الحرارة {b.get('temperature', 0):.1f}°C | الفولتية {b.get('voltage')}mV",
                          "البطارية معطوبة أو تسخن؛ افصل الشاحن واستبدلها." if st == BAD else ""))
        cyc = b.get("cycleCount") or 0
        if cyc > 0:
            out.append(Result(g, "دورات الشحن", BAD if cyc > 800 else WARN if cyc > 500 else OK, f"{cyc} دورة"))
    sy = d.get("system") or {}
    if sy:
        out.append(Result(g, "معلومات الجهاز", INFO, f"{sy.get('manufacturer', '')} {sy.get('model', '')} | أندرويد {sy.get('release')}"
                          + (f" | {sy.get('cores')} أنوية {sy.get('hardware', '')}" if sy.get("cores") else "")))
        if sy.get("ramTotal"):
            used = 100 - sy.get("ramAvail", 0) * 100 / sy["ramTotal"]
            out.append(Result(g, "الرام", WARN if used > 90 else OK, f"الإجمالي {fmt_size(sy['ramTotal'])} | مستخدم {used:.0f}%"))
        if sy.get("diskTotal"):
            pct = sy.get("diskFree", 0) * 100 / sy["diskTotal"]
            out.append(Result(g, "التخزين", BAD if pct < 5 else WARN if pct < 15 else OK, f"الفارغ {fmt_size(sy.get('diskFree', 0))} من {fmt_size(sy['diskTotal'])} ({pct:.0f}%)"))
        if (sy.get("thermal") or 0) >= 2:
            out.append(Result(g, "الحرارة العامة", BAD if sy["thermal"] >= 3 else WARN, f"المستوى {sy['thermal']}"))
    types = {x.get("type") for x in (d.get("sensors") or [])}
    for t, label, st in ((1, "مقياس التسارع", BAD), (4, "الجيروسكوب", WARN), (2, "البوصلة", WARN), (5, "حساس الإضاءة", WARN), (8, "حساس القرب", WARN)):
        if (d.get("sensors") is not None) and t not in types:
            out.append(Result(g, f"حساس مفقود: {label}", st, "غير ظاهر للنظام", "إذا الموديل يفترض فيه هذا الحساس فهو معطّل."))
    stg = d.get("storage")
    if stg:
        st = BAD if not stg.get("ok") else WARN if stg.get("writeMBs", 99) < 10 else OK
        out.append(Result(g, "اختبار التخزين", st, ("البيانات سليمة" if stg.get("ok") else "البيانات تالفة!") + f" | كتابة {stg.get('writeMBs', 0):.0f}MB/s"))
    return out


def remote_scan(ip, pin, deep=False, port=8765, timeout=900):
    """يفحص جهازًا آخر (كمبيوتر أو موبايل) يعمل بوضع الاستقبال. يرجع (معلومات الجهاز، نتائج)."""
    info = remote_ping(ip, port)
    if not info:
        raise ConnectionError("لا يوجد رد: تأكد من وضع الاستقبال والشبكة والـFirewall")
    is_pc = info.get("type") == "pc"
    path = "/scan" if is_pc else "/report"
    url = f"http://{ip}:{port}{path}?pin={urllib.parse.quote(pin)}" + ("&deep=1" if deep and is_pc else "")
    try:
        d = _http_json(url, timeout)
    except urllib.error.HTTPError as e:
        raise PermissionError({403: "رمز PIN غلط", 429: "الجهاز مشغول أو مقفل مؤقتًا بسبب محاولات PIN خاطئة"}.get(e.code, f"خطأ {e.code}"))
    name = info.get("name") or ip
    if is_pc:
        res = [Result(f"[{name}]", r["name"], r["status"], r.get("details", ""), r.get("fix", "")) for r in d.get("results", [])]
    else:
        res = phone_report_results(d, name)
    return info, res


def local_subnets():
    ips = set()
    try:
        for fam, _, _, _, sa in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ips.add(sa[0])
    except OSError:
        pass
    ips.add(local_ip())
    return sorted(i for i in ips if not i.startswith("127."))


def discover_lan(port=8765, timeout=0.4, workers=128, progress=None, extra_ips=None):
    """يبحث عن أجهزة بوضع الاستقبال بالشبكة المحلية (/24 لكل واجهة). يرجع قائمة (ip, ping_info)."""
    from concurrent.futures import ThreadPoolExecutor
    mine = set(local_subnets())
    cands = []
    for ip in list(mine) + list(extra_ips or []):
        p = ip.split(".")
        if len(p) != 4:
            continue
        for h in range(1, 255):
            c = f"{p[0]}.{p[1]}.{p[2]}.{h}"
            if c not in mine and c not in cands:
                cands.append(c)

    def probe(ip):
        try:
            with socket.create_connection((ip, port), timeout=timeout):
                pass
        except OSError:
            return None
        info = remote_ping(ip, port, 2)
        return (ip, info) if info else None

    found = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for i, r in enumerate(ex.map(probe, cands)):
            if r:
                found.append(r)
            if progress and i % 50 == 0:
                progress(i, len(cands))
    return found
