#!/usr/bin/env python3
"""يُشغَّل بعد `npx cap add android`: ينسخ البلجن الأصلي ويسجّله ويضيف الصلاحيات."""
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ANDROID = os.path.join(HERE, "..", "android")
PKG_DIR = os.path.join(ANDROID, "app", "src", "main", "java", "com", "devicedoctor", "app")
MANIFEST = os.path.join(ANDROID, "app", "src", "main", "AndroidManifest.xml")

os.makedirs(PKG_DIR, exist_ok=True)
shutil.copy(os.path.join(HERE, "DeviceDoctorPlugin.java"), os.path.join(PKG_DIR, "DeviceDoctorPlugin.java"))

with open(os.path.join(PKG_DIR, "MainActivity.java"), "w", encoding="utf-8") as f:
    f.write("""package com.devicedoctor.app;

import android.os.Bundle;
import com.getcapacitor.BridgeActivity;

public class MainActivity extends BridgeActivity {
    @Override
    public void onCreate(Bundle savedInstanceState) {
        registerPlugin(DeviceDoctorPlugin.class);
        super.onCreate(savedInstanceState);
    }
}
""")

with open(MANIFEST, "r", encoding="utf-8") as f:
    m = f.read()

extra = """
    <uses-permission android:name="android.permission.CAMERA" />
    <uses-permission android:name="android.permission.RECORD_AUDIO" />
    <uses-permission android:name="android.permission.MODIFY_AUDIO_SETTINGS" />
    <uses-permission android:name="android.permission.VIBRATE" />
    <uses-feature android:name="android.hardware.camera" android:required="false" />
    <uses-feature android:name="android.hardware.microphone" android:required="false" />
"""
for line in re.findall(r"<uses-permission[^>]*/>|<uses-feature[^>]*/>", extra):
    name = re.search(r'android:name="([^"]+)"', line).group(1)
    if name in m:
        extra = extra.replace(line, "")
m = m.replace("<application", extra + "\n    <application", 1)
with open(MANIFEST, "w", encoding="utf-8") as f:
    f.write(m)
print("patched OK")
