package com.devicedoctor.app;

import android.app.ActivityManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.Sensor;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
import android.media.AudioAttributes;
import android.media.AudioFormat;
import android.media.AudioManager;
import android.media.AudioTrack;
import android.os.BatteryManager;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.os.PowerManager;
import android.os.StatFs;
import android.os.SystemClock;

import com.getcapacitor.JSArray;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

import java.io.BufferedReader;
import java.io.File;
import java.io.RandomAccessFile;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.NetworkInterface;
import java.net.ServerSocket;
import java.net.Socket;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.LongBuffer;
import java.security.SecureRandom;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Locale;
import java.util.Random;
import java.util.zip.CRC32;

@CapacitorPlugin(name = "DeviceDoctor")
public class DeviceDoctorPlugin extends Plugin {

    private ServerSocket server;
    private String pin = "";

    // ───────────── جمع البيانات (يُستخدم محليًا وفي وضع الاستقبال) ─────────────
    private JSObject batteryObj() {
        Intent i = getContext().registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        JSObject r = new JSObject();
        if (i == null) {
            r.put("available", false);
            return r;
        }
        int level = i.getIntExtra(BatteryManager.EXTRA_LEVEL, -1);
        int scale = i.getIntExtra(BatteryManager.EXTRA_SCALE, 100);
        r.put("available", true);
        r.put("level", scale > 0 ? (level * 100) / scale : level);
        r.put("temperature", i.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) / 10.0);
        r.put("voltage", i.getIntExtra(BatteryManager.EXTRA_VOLTAGE, 0));
        r.put("health", i.getIntExtra(BatteryManager.EXTRA_HEALTH, 1));
        r.put("status", i.getIntExtra(BatteryManager.EXTRA_STATUS, 1));
        r.put("plugged", i.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0));
        r.put("present", i.getBooleanExtra(BatteryManager.EXTRA_PRESENT, true));
        String tech = i.getStringExtra(BatteryManager.EXTRA_TECHNOLOGY);
        r.put("technology", tech == null ? "" : tech);
        r.put("cycleCount", i.getIntExtra("android.os.extra.CYCLE_COUNT", -1));
        try {
            BatteryManager bm = (BatteryManager) getContext().getSystemService(Context.BATTERY_SERVICE);
            r.put("currentNow", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW));
            r.put("chargeCounter", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER));
        } catch (Exception e) {
            // بعض الأجهزة لا تدعمها
        }
        return r;
    }

    private JSObject systemObj() {
        Context c = getContext();
        JSObject r = new JSObject();
        ActivityManager am = (ActivityManager) c.getSystemService(Context.ACTIVITY_SERVICE);
        ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
        am.getMemoryInfo(mi);
        r.put("ramTotal", mi.totalMem);
        r.put("ramAvail", mi.availMem);
        try {
            StatFs fs = new StatFs(c.getFilesDir().getAbsolutePath());
            r.put("diskTotal", fs.getTotalBytes());
            r.put("diskFree", fs.getAvailableBytes());
        } catch (Exception e) {
            r.put("diskTotal", 0);
            r.put("diskFree", 0);
        }
        r.put("uptimeMs", SystemClock.elapsedRealtime());
        r.put("sdk", Build.VERSION.SDK_INT);
        r.put("release", Build.VERSION.RELEASE);
        r.put("model", Build.MODEL);
        r.put("manufacturer", Build.MANUFACTURER);
        r.put("patch", Build.VERSION.SECURITY_PATCH == null ? "" : Build.VERSION.SECURITY_PATCH);
        int thermal = -1;
        if (Build.VERSION.SDK_INT >= 29) {
            PowerManager pm = (PowerManager) c.getSystemService(Context.POWER_SERVICE);
            thermal = pm.getCurrentThermalStatus();
        }
        r.put("thermal", thermal);
        boolean rooted = new File("/system/bin/su").exists() || new File("/system/xbin/su").exists()
                || new File("/sbin/su").exists();
        r.put("rooted", rooted);
        r.put("cores", Runtime.getRuntime().availableProcessors());
        r.put("hardware", Build.HARDWARE == null ? "" : Build.HARDWARE);
        r.put("board", Build.BOARD == null ? "" : Build.BOARD);
        r.put("abi", (Build.SUPPORTED_ABIS != null && Build.SUPPORTED_ABIS.length > 0) ? Build.SUPPORTED_ABIS[0] : "");
        android.util.DisplayMetrics dm = c.getResources().getDisplayMetrics();
        r.put("dispW", dm.widthPixels);
        r.put("dispH", dm.heightPixels);
        r.put("dpi", dm.densityDpi);
        return r;
    }

    private JSArray sensorsArr() {
        SensorManager sm = (SensorManager) getContext().getSystemService(Context.SENSOR_SERVICE);
        List<Sensor> list = sm.getSensorList(Sensor.TYPE_ALL);
        JSArray arr = new JSArray();
        for (Sensor s : list) {
            JSObject o = new JSObject();
            o.put("name", s.getName());
            o.put("type", s.getType());
            o.put("vendor", s.getVendor());
            arr.put(o);
        }
        return arr;
    }

    // ───────────── تشغيل نغمة من سماعة الأذن أو السبيكر الخارجي ─────────────
    @PluginMethod
    public void playTone(final PluginCall call) {
        final String route = call.getString("route", "speaker");
        new Thread(new Runnable() {
            @Override
            public void run() {
                AudioManager am = (AudioManager) getContext().getSystemService(Context.AUDIO_SERVICE);
                int oldMode = am.getMode();
                boolean oldSp = am.isSpeakerphoneOn();
                AudioTrack t = null;
                try {
                    boolean ear = "earpiece".equals(route);
                    if (ear) {
                        am.setMode(AudioManager.MODE_IN_COMMUNICATION);
                        am.setSpeakerphoneOn(false);
                    } else {
                        am.setMode(AudioManager.MODE_NORMAL);
                        am.setSpeakerphoneOn(true);
                    }
                    int rate = 44100;
                    int n = rate * 2;
                    short[] buf = new short[n];
                    for (int i = 0; i < n; i++) {
                        buf[i] = (short) (Math.sin(2 * Math.PI * 440.0 * i / rate) * 20000);
                    }
                    AudioAttributes aa = new AudioAttributes.Builder()
                            .setUsage(ear ? AudioAttributes.USAGE_VOICE_COMMUNICATION : AudioAttributes.USAGE_MEDIA)
                            .setContentType(ear ? AudioAttributes.CONTENT_TYPE_SPEECH : AudioAttributes.CONTENT_TYPE_MUSIC)
                            .build();
                    AudioFormat af = new AudioFormat.Builder()
                            .setSampleRate(rate)
                            .setEncoding(AudioFormat.ENCODING_PCM_16BIT)
                            .setChannelMask(AudioFormat.CHANNEL_OUT_MONO)
                            .build();
                    t = new AudioTrack(aa, af, n * 2, AudioTrack.MODE_STATIC, AudioManager.AUDIO_SESSION_ID_GENERATE);
                    t.write(buf, 0, n);
                    t.play();
                    Thread.sleep(2100);
                    JSObject r = new JSObject();
                    r.put("played", true);
                    call.resolve(r);
                } catch (Exception e) {
                    call.reject(String.valueOf(e.getMessage()));
                } finally {
                    try { if (t != null) { t.stop(); t.release(); } } catch (Exception ignore) { }
                    try { am.setMode(oldMode); am.setSpeakerphoneOn(oldSp); } catch (Exception ignore) { }
                }
            }
        }).start();
    }

    // ───────────── اختبار الكتابة والقراءة على التخزين ─────────────
    private JSObject storageObj() throws Exception {
        File f = new File(getContext().getCacheDir(), "dd_test.bin");
        try {
            int mb = 32;
            byte[] blk = new byte[1 << 20];
            new Random(42).nextBytes(blk);
            CRC32 w = new CRC32();
            long t0 = SystemClock.elapsedRealtime();
            FileOutputStream o = new FileOutputStream(f);
            for (int i = 0; i < mb; i++) {
                o.write(blk);
                w.update(blk, 0, blk.length);
            }
            o.getFD().sync();
            o.close();
            long t1 = SystemClock.elapsedRealtime();
            CRC32 rd = new CRC32();
            FileInputStream in = new FileInputStream(f);
            byte[] b = new byte[1 << 20];
            int k;
            long tot = 0;
            while ((k = in.read(b)) > 0) {
                rd.update(b, 0, k);
                tot += k;
            }
            in.close();
            long t2 = SystemClock.elapsedRealtime();
            JSObject r = new JSObject();
            r.put("ok", w.getValue() == rd.getValue() && tot == ((long) mb << 20));
            r.put("writeMBs", mb * 1000.0 / Math.max(1, t1 - t0));
            r.put("readMBs", mb * 1000.0 / Math.max(1, t2 - t1));
            try {
                RandomAccessFile raf = new RandomAccessFile(f, "rwd");
                byte[] b4 = new byte[4096];
                Random rnd = new Random(7);
                int n = 300;
                long t3 = SystemClock.elapsedRealtime();
                for (int i = 0; i < n; i++) {
                    raf.seek((long) rnd.nextInt(mb * 256) * 4096L);
                    raf.write(b4);
                }
                raf.close();
                r.put("syncIops", n * 1000.0 / Math.max(1, SystemClock.elapsedRealtime() - t3));
            } catch (Exception ignored) {
                // اختياري
            }
            return r;
        } finally {
            f.delete();
        }
    }

    @PluginMethod
    public void storageTest(final PluginCall call) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    call.resolve(storageObj());
                } catch (Exception e) {
                    call.reject(String.valueOf(e.getMessage()));
                }
            }
        }).start();
    }

    // ───────────── كل عناوين IP (واي فاي / USB tethering / ...) ─────────────
    private JSArray ipsArr() {
        JSArray arr = new JSArray();
        try {
            for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                if (!ni.isUp() || ni.isLoopback()) continue;
                for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                    if (a instanceof Inet4Address && !a.isLoopbackAddress() && a.isSiteLocalAddress()) {
                        JSObject o = new JSObject();
                        o.put("name", ni.getName());
                        o.put("ip", a.getHostAddress());
                        arr.put(o);
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return arr;
    }

    @PluginMethod
    public void localIps(PluginCall call) {
        JSObject r = new JSObject();
        r.put("ips", ipsArr());
        call.resolve(r);
    }

    // ───────────── فحص جهاز موصول بكيبل USB (OTG) عبر ADB ─────────────
    private static final String[][] ADB_CMDS = {
        {"battery", "dumpsys battery"},
        {"props", "getprop"},
        {"mem", "cat /proc/meminfo"},
        {"df", "df /data"},
        {"uptime", "cat /proc/uptime"},
        {"cpu", "grep -c ^processor /proc/cpuinfo"},
        {"sensors", "dumpsys sensorservice"},
        {"thermal", "dumpsys thermalservice"},
        {"camera", "dumpsys media.camera"},
        {"wm", "wm size; wm density"},
        {"crashes", "logcat -d -b crash -t 500 | grep -c 'FATAL EXCEPTION'"},
        {"anr", "logcat -d -b main -t 3000 | grep -c 'ANR in'"},
        {"psy", "for f in charge_full charge_full_design cycle_count capacity current_now voltage_now temp health technology; do echo $f=$(cat /sys/class/power_supply/battery/$f 2>/dev/null); done"}
    };

    @PluginMethod
    public void adbUsbScan(final PluginCall call) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                AdbClient c = null;
                try {
                    UsbManager um = (UsbManager) getContext().getSystemService(Context.USB_SERVICE);
                    UsbDevice d = UsbAdb.find(um);
                    if (d == null) {
                        call.reject("no_device");
                        return;
                    }
                    if (!UsbAdb.requestPermission(getContext(), um, d, 30000)) {
                        call.reject("no_permission");
                        return;
                    }
                    UsbInterface intf = UsbAdb.adbInterface(d);
                    UsbDeviceConnection conn = um.openDevice(d);
                    if (conn == null) {
                        call.reject("open_failed");
                        return;
                    }
                    UsbAdb.Transport tr;
                    try {
                        tr = new UsbAdb.Transport(conn, intf);
                    } catch (Exception e) {
                        conn.close();
                        call.reject("usb: " + e.getMessage());
                        return;
                    }
                    c = new AdbClient(tr, AdbClient.loadOrCreateKeys(getContext().getFilesDir()));
                    try {
                        c.connect(60000);
                    } catch (Exception e) {
                        call.reject("auth: " + e.getMessage());
                        return;
                    }
                    JSObject r = new JSObject();
                    r.put("banner", c.deviceBanner);
                    r.put("usbName", String.valueOf(d.getProductName()));
                    for (String[] cm : ADB_CMDS) {
                        String out = "";
                        try {
                            out = c.shell(cm[1], 20000);
                        } catch (Exception e) {
                            out = "";
                        }
                        r.put(cm[0], out);
                    }
                    try {
                        long t0 = SystemClock.elapsedRealtime();
                        String dd = c.shell("dd if=/dev/zero of=/data/local/tmp/dd_t bs=1048576 count=32 conv=fsync 2>&1; echo EXIT=$?; ls -l /data/local/tmp/dd_t; rm -f /data/local/tmp/dd_t", 60000);
                        r.put("ddMs", SystemClock.elapsedRealtime() - t0);
                        r.put("dd", dd);
                    } catch (Exception e) {
                        r.put("dd", "");
                    }
                    call.resolve(r);
                } catch (Exception e) {
                    call.reject(String.valueOf(e.getMessage()));
                } finally {
                    if (c != null) c.close();
                }
            }
        }).start();
    }

    // ───────────── فحص البطارية العميق (قراءات حقيقية من النظام) ─────────────
    private String readSys(String path) {
        try {
            BufferedReader r = new BufferedReader(new InputStreamReader(new java.io.FileInputStream(path)));
            String l = r.readLine();
            r.close();
            return l == null ? "" : l.trim();
        } catch (Exception e) {
            return "";
        }
    }

    private JSObject batteryDeepObj() {
        JSObject r = new JSObject();
        JSObject sys = new JSObject();
        String[] bases = {"/sys/class/power_supply/battery/", "/sys/class/power_supply/bms/"};
        String[] keys = {"charge_full", "charge_full_design", "charge_counter", "energy_full", "energy_full_design", "capacity",
                "cycle_count", "battery_cycle", "health", "status", "current_now", "voltage_now", "temp", "technology",
                "batt_soc", "fg_fullcapnom", "fg_fullcaprep", "batt_cap_max"};
        for (String b : bases) {
            for (String k : keys) {
                String v = readSys(b + k);
                if (v.length() > 0 && !sys.has(k)) sys.put(k, v);
            }
        }
        r.put("sysfs", sys);
        double design = -1;
        try {
            Class<?> c = Class.forName("com.android.internal.os.PowerProfile");
            Object pp = c.getConstructor(Context.class).newInstance(getContext());
            design = (Double) c.getMethod("getBatteryCapacity").invoke(pp);
        } catch (Throwable ignored) {
            // API مخفي: قد لا يعمل على بعض الإصدارات
        }
        r.put("designMah", design);
        try {
            BatteryManager bm = (BatteryManager) getContext().getSystemService(Context.BATTERY_SERVICE);
            if (Build.VERSION.SDK_INT >= 34) r.put("soh", bm.getIntProperty(10));
            r.put("currentAvg", bm.getIntProperty(3));
            r.put("energyCounter", bm.getLongProperty(5));
        } catch (Throwable ignored) {
            // غير مدعوم
        }
        JSObject b = batteryObj();
        r.put("battery", b);
        Intent i = getContext().registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        if (i != null) {
            r.put("maxChargeCurrent", i.getIntExtra("max_charging_current", -1));
            r.put("maxChargeVoltage", i.getIntExtra("max_charging_voltage", -1));
        }
        return r;
    }

    @PluginMethod
    public void batteryDeep(PluginCall call) {
        call.resolve(batteryDeepObj());
    }

    private JSObject sampleBattery(BatteryManager bm, String phase, long t) {
        JSObject o = new JSObject();
        Intent i = getContext().registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        o.put("phase", phase);
        o.put("t", t);
        if (i != null) {
            o.put("v", i.getIntExtra(BatteryManager.EXTRA_VOLTAGE, 0));
            o.put("temp", i.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0));
            o.put("plugged", i.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0));
        }
        try {
            o.put("i", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CURRENT_NOW));
            o.put("cc", bm.getIntProperty(BatteryManager.BATTERY_PROPERTY_CHARGE_COUNTER));
        } catch (Throwable ignored) {
            // بعض الأجهزة لا تدعم
        }
        return o;
    }

    /** اختبار حمل: يقيس هبوط الفولتية والتيار والحرارة وتراجع أداء المعالج. */
    @PluginMethod
    public void loadTest(final PluginCall call) {
        final int secs = Math.max(8, Math.min(40, call.getInt("seconds", 20)));
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    BatteryManager bm = (BatteryManager) getContext().getSystemService(Context.BATTERY_SERVICE);
                    JSArray samples = new JSArray();
                    long start = SystemClock.elapsedRealtime();
                    for (int k = 0; k < 6; k++) {
                        samples.put(sampleBattery(bm, "idle", SystemClock.elapsedRealtime() - start));
                        Thread.sleep(500);
                    }
                    final long loadStart = SystemClock.elapsedRealtime();
                    final long[] buckets = new long[secs + 4];
                    final boolean[] stop = {false};
                    int cores = Math.max(1, Runtime.getRuntime().availableProcessors());
                    ArrayList<Thread> ts = new ArrayList<Thread>();
                    for (int c = 0; c < cores; c++) {
                        Thread th = new Thread(new Runnable() {
                            @Override
                            public void run() {
                                double x = 1.0001;
                                while (!stop[0]) {
                                    for (int k = 0; k < 20000; k++) {
                                        x = x * 1.0000001 + Math.sqrt(x);
                                    }
                                    if (x > 1e300) x = 1.0001;
                                    int b = (int) ((SystemClock.elapsedRealtime() - loadStart) / 1000);
                                    synchronized (buckets) {
                                        if (b >= 0 && b < buckets.length) buckets[b]++;
                                    }
                                }
                            }
                        });
                        th.setDaemon(true);
                        ts.add(th);
                        th.start();
                    }
                    while (SystemClock.elapsedRealtime() - loadStart < secs * 1000L) {
                        samples.put(sampleBattery(bm, "load", SystemClock.elapsedRealtime() - start));
                        Thread.sleep(500);
                    }
                    stop[0] = true;
                    for (Thread th : ts) th.join(2000);
                    Thread.sleep(300);
                    samples.put(sampleBattery(bm, "after", SystemClock.elapsedRealtime() - start));
                    JSArray bk = new JSArray();
                    synchronized (buckets) {
                        for (int k = 0; k < secs; k++) bk.put((double) buckets[k]);
                    }
                    JSObject r = new JSObject();
                    r.put("samples", samples);
                    r.put("buckets", bk);
                    r.put("cores", cores);
                    r.put("seconds", secs);
                    call.resolve(r);
                } catch (Throwable e) {
                    call.reject(String.valueOf(e.getMessage()));
                }
            }
        }).start();
    }

    private long meminfoKb(String key) {
        try {
            BufferedReader r = new BufferedReader(new InputStreamReader(new java.io.FileInputStream("/proc/meminfo")));
            String l;
            while ((l = r.readLine()) != null) {
                if (l.startsWith(key + ":")) {
                    r.close();
                    return Long.parseLong(l.replaceAll("[^0-9]", ""));
                }
            }
            r.close();
        } catch (Exception ignored) {
            // لا شيء
        }
        return -1;
    }

    /** اختبار الرام: كتابة أنماط وقراءتها على ذاكرة native + قياس السرعة. */
    @PluginMethod
    public void ramTest(final PluginCall call) {
        final int reqMb = call.getInt("mb", 512);
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    ActivityManager am = (ActivityManager) getContext().getSystemService(Context.ACTIVITY_SERVICE);
                    ActivityManager.MemoryInfo mi = new ActivityManager.MemoryInfo();
                    am.getMemoryInfo(mi);
                    long availMb = mi.availMem / (1024 * 1024);
                    int mb = (int) Math.max(64, Math.min(reqMb, availMb * 4 / 10));
                    ArrayList<LongBuffer> bufs = new ArrayList<LongBuffer>();
                    int chunk = 32;
                    try {
                        for (int got = 0; got + chunk <= mb; got += chunk) {
                            bufs.add(ByteBuffer.allocateDirect(chunk << 20).order(ByteOrder.nativeOrder()).asLongBuffer());
                        }
                    } catch (OutOfMemoryError e) {
                        // نكمل بما حجزناه
                    }
                    long errors = 0;
                    long[] pats = {0xAAAAAAAAAAAAAAAAL, 0x5555555555555555L, -1L, 0L};
                    long wNs = 0, rNs = 0, bytes = 0;
                    for (int p = 0; p <= pats.length; p++) {
                        long t0 = System.nanoTime();
                        for (LongBuffer lb : bufs) {
                            int n = lb.capacity();
                            if (p < pats.length) {
                                long v = pats[p];
                                for (int i = 0; i < n; i++) lb.put(i, v);
                            } else {
                                for (int i = 0; i < n; i++) lb.put(i, i * 0x9E3779B97F4A7C15L);
                            }
                            bytes += (long) n * 8;
                        }
                        long t1 = System.nanoTime();
                        for (LongBuffer lb : bufs) {
                            int n = lb.capacity();
                            if (p < pats.length) {
                                long v = pats[p];
                                for (int i = 0; i < n; i++) if (lb.get(i) != v) errors++;
                            } else {
                                for (int i = 0; i < n; i++) if (lb.get(i) != i * 0x9E3779B97F4A7C15L) errors++;
                            }
                        }
                        long t2 = System.nanoTime();
                        wNs += t1 - t0;
                        rNs += t2 - t1;
                    }
                    long testedMb = (long) bufs.size() * chunk;
                    bufs.clear();
                    JSObject r = new JSObject();
                    r.put("testedMb", testedMb);
                    r.put("errors", errors);
                    double mbTot = bytes / 1048576.0;
                    r.put("writeMBs", mbTot * 1e9 / Math.max(1, wNs));
                    r.put("readMBs", mbTot * 1e9 / Math.max(1, rNs));
                    r.put("lowMemory", mi.lowMemory);
                    r.put("swapTotalKb", meminfoKb("SwapTotal"));
                    r.put("swapFreeKb", meminfoKb("SwapFree"));
                    r.put("memTotalKb", meminfoKb("MemTotal"));
                    r.put("memAvailKb", meminfoKb("MemAvailable"));
                    call.resolve(r);
                } catch (Throwable e) {
                    call.reject(String.valueOf(e.getMessage()));
                }
            }
        }).start();
    }

    @PluginMethod
    public void features(PluginCall call) {
        android.content.pm.PackageManager pm = getContext().getPackageManager();
        String[] f = {"android.hardware.wifi", "android.hardware.bluetooth", "android.hardware.nfc", "android.hardware.location.gps",
                "android.hardware.fingerprint", "android.hardware.telephony", "android.hardware.camera.flash",
                "android.hardware.camera.front", "android.hardware.sensor.barometer", "android.hardware.usb.host"};
        JSObject r = new JSObject();
        for (String k : f) r.put(k.replace("android.hardware.", ""), pm.hasSystemFeature(k));
        call.resolve(r);
    }

    @PluginMethod
    public void getBattery(PluginCall call) {
        call.resolve(batteryObj());
    }

    @PluginMethod
    public void getSystem(PluginCall call) {
        call.resolve(systemObj());
    }

    @PluginMethod
    public void getSensors(PluginCall call) {
        JSObject r = new JSObject();
        r.put("sensors", sensorsArr());
        call.resolve(r);
    }

    @PluginMethod
    public void readSensor(final PluginCall call) {
        final int type = call.getInt("type", Sensor.TYPE_ACCELEROMETER);
        final SensorManager sm = (SensorManager) getContext().getSystemService(Context.SENSOR_SERVICE);
        final Sensor sensor = sm.getDefaultSensor(type);
        if (sensor == null) {
            JSObject r = new JSObject();
            r.put("available", false);
            call.resolve(r);
            return;
        }
        final float[][] last = new float[1][];
        final int[] count = new int[1];
        final SensorEventListener l = new SensorEventListener() {
            @Override
            public void onSensorChanged(SensorEvent e) {
                last[0] = e.values.clone();
                count[0]++;
            }

            @Override
            public void onAccuracyChanged(Sensor s, int a) {
            }
        };
        sm.registerListener(l, sensor, SensorManager.SENSOR_DELAY_NORMAL);
        new Handler(Looper.getMainLooper()).postDelayed(new Runnable() {
            @Override
            public void run() {
                sm.unregisterListener(l);
                JSObject r = new JSObject();
                r.put("available", true);
                r.put("samples", count[0]);
                r.put("maxRange", sensor.getMaximumRange());
                JSArray v = new JSArray();
                if (last[0] != null) {
                    for (float f : last[0]) {
                        v.put(Double.valueOf(f));
                    }
                }
                r.put("values", v);
                call.resolve(r);
            }
        }, 1500);
    }

    // ───────────── وضع الاستقبال: سيرفر HTTP صغير على الواي فاي ─────────────
    private String localIp() {
        try {
            for (int pass = 0; pass < 2; pass++) {
                for (NetworkInterface ni : Collections.list(NetworkInterface.getNetworkInterfaces())) {
                    if (!ni.isUp() || ni.isLoopback()) continue;
                    String n = ni.getName();
                    boolean wifi = n.startsWith("wlan") || n.startsWith("swlan") || n.startsWith("ap");
                    if (pass == 0 && !wifi) continue;
                    for (InetAddress a : Collections.list(ni.getInetAddresses())) {
                        if (a instanceof Inet4Address && !a.isLoopbackAddress() && a.isSiteLocalAddress()) {
                            return a.getHostAddress();
                        }
                    }
                }
            }
        } catch (Exception ignored) {
        }
        return "";
    }

    private void stopServerInternal() {
        try {
            if (server != null) server.close();
        } catch (Exception ignored) {
        }
        server = null;
    }

    @PluginMethod
    public void startServer(PluginCall call) {
        int port = call.getInt("port", 8765);
        stopServerInternal();
        try {
            server = new ServerSocket(port);
        } catch (Exception e) {
            call.reject("تعذر فتح المنفذ " + port + ": " + e.getMessage());
            return;
        }
        pin = String.format(Locale.US, "%04d", new SecureRandom().nextInt(10000));
        final ServerSocket ss = server;
        Thread t = new Thread(new Runnable() {
            @Override
            public void run() {
                while (!ss.isClosed()) {
                    try {
                        Socket s = ss.accept();
                        handle(s);
                    } catch (Exception e) {
                        break;
                    }
                }
            }
        });
        t.setDaemon(true);
        t.start();
        JSObject r = new JSObject();
        r.put("ip", localIp());
        r.put("ips", ipsArr());
        r.put("port", port);
        r.put("pin", pin);
        call.resolve(r);
    }

    @PluginMethod
    public void stopServer(PluginCall call) {
        stopServerInternal();
        call.resolve();
    }

    private boolean pinOk(String query) {
        if (query == null) return false;
        for (String kv : query.split("&")) {
            int e = kv.indexOf('=');
            if (e > 0 && kv.substring(0, e).equals("pin") && kv.substring(e + 1).equals(pin)) return true;
        }
        return false;
    }

    private void handle(Socket s) {
        try {
            s.setSoTimeout(5000);
            BufferedReader in = new BufferedReader(new InputStreamReader(s.getInputStream(), "UTF-8"));
            String line = in.readLine();
            String h;
            while ((h = in.readLine()) != null && h.length() > 0) {
                // نتجاهل الهيدرز
            }
            String path = "/";
            String query = "";
            if (line != null) {
                String[] p = line.split(" ");
                if (p.length >= 2) {
                    String full = p[1];
                    int q = full.indexOf('?');
                    path = q >= 0 ? full.substring(0, q) : full;
                    query = q >= 0 ? full.substring(q + 1) : "";
                }
            }
            int code = 200;
            JSObject body = new JSObject();
            String name = Build.MANUFACTURER + " " + Build.MODEL;
            if (path.equals("/ping")) {
                body.put("type", "phone");
                body.put("name", name);
            } else if (path.equals("/report")) {
                if (!pinOk(query)) {
                    code = 403;
                    body.put("error", "bad pin");
                } else {
                    body.put("type", "phone");
                    body.put("name", name);
                    body.put("battery", batteryObj());
                    body.put("system", systemObj());
                    body.put("sensors", sensorsArr());
                    try {
                        body.put("batteryDeep", batteryDeepObj());
                    } catch (Exception e) {
                        // اختياري
                    }
                    try {
                        body.put("storage", storageObj());
                    } catch (Exception e) {
                        // اختياري
                    }
                }
            } else {
                code = 404;
                body.put("error", "not found");
            }
            byte[] b = body.toString().getBytes("UTF-8");
            String head = "HTTP/1.1 " + code + (code == 200 ? " OK" : " Error")
                    + "\r\nContent-Type: application/json; charset=utf-8"
                    + "\r\nAccess-Control-Allow-Origin: *"
                    + "\r\nContent-Length: " + b.length
                    + "\r\nConnection: close\r\n\r\n";
            OutputStream out = s.getOutputStream();
            out.write(head.getBytes("UTF-8"));
            out.write(b);
            out.flush();
        } catch (Exception ignored) {
        } finally {
            try {
                s.close();
            } catch (Exception ignored) {
            }
        }
    }
}
