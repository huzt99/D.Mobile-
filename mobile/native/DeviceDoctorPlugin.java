package com.devicedoctor.app;

import android.app.ActivityManager;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.Sensor;
import android.hardware.SensorEvent;
import android.hardware.SensorEventListener;
import android.hardware.SensorManager;
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

import java.io.File;
import java.util.List;

@CapacitorPlugin(name = "DeviceDoctor")
public class DeviceDoctorPlugin extends Plugin {

    @PluginMethod
    public void getBattery(PluginCall call) {
        Intent i = getContext().registerReceiver(null, new IntentFilter(Intent.ACTION_BATTERY_CHANGED));
        JSObject r = new JSObject();
        if (i == null) {
            r.put("available", false);
            call.resolve(r);
            return;
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
        call.resolve(r);
    }

    @PluginMethod
    public void getSystem(PluginCall call) {
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
        call.resolve(r);
    }

    @PluginMethod
    public void getSensors(PluginCall call) {
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
        JSObject r = new JSObject();
        r.put("sensors", arr);
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
}
