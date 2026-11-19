# -*- coding: utf-8 -*-
"""محرك الفحص (بدون واجهة) — يفحص الكمبيوتر (ويندوز) والموبايل (أندرويد عبر ADB)."""
import datetime
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
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


def pc_checks():
    return [("الأقراص", check_disks), ("مساحة التخزين", check_space), ("الذاكرة", check_ram),
            ("المعالج", check_cpu), ("البطارية", check_battery), ("الأجهزة المعطلة", check_devices),
            ("كرت الشاشة", check_gpu), ("أخطاء العتاد", check_events), ("الشاشة الزرقاء", check_bsod),
            ("الشبكة", check_network), ("معلومات النظام", check_system)]


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
