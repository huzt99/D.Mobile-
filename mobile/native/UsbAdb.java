package com.devicedoctor.app;

import android.app.PendingIntent;
import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.content.IntentFilter;
import android.hardware.usb.UsbConstants;
import android.hardware.usb.UsbDevice;
import android.hardware.usb.UsbDeviceConnection;
import android.hardware.usb.UsbEndpoint;
import android.hardware.usb.UsbInterface;
import android.hardware.usb.UsbManager;
import android.os.Build;

import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;

/** ربط ADB بمنفذ USB (الموبايل هو الـHost والجهاز الآخر موصول بكيبل OTG). */
public class UsbAdb {

    static final String ACTION = "com.devicedoctor.app.USB_PERMISSION";

    public static UsbInterface adbInterface(UsbDevice d) {
        for (int i = 0; i < d.getInterfaceCount(); i++) {
            UsbInterface f = d.getInterface(i);
            if (f.getInterfaceClass() == 255 && f.getInterfaceSubclass() == 66 && f.getInterfaceProtocol() == 1) {
                return f;
            }
        }
        return null;
    }

    public static UsbDevice find(UsbManager um) {
        for (UsbDevice d : um.getDeviceList().values()) {
            if (adbInterface(d) != null) return d;
        }
        return null;
    }

    public static boolean requestPermission(Context c, UsbManager um, UsbDevice d, int waitMs) throws InterruptedException {
        if (um.hasPermission(d)) return true;
        final CountDownLatch latch = new CountDownLatch(1);
        BroadcastReceiver r = new BroadcastReceiver() {
            @Override
            public void onReceive(Context ctx, Intent intent) {
                latch.countDown();
            }
        };
        IntentFilter f = new IntentFilter(ACTION);
        if (Build.VERSION.SDK_INT >= 33) {
            c.registerReceiver(r, f, Context.RECEIVER_NOT_EXPORTED);
        } else {
            c.registerReceiver(r, f);
        }
        try {
            Intent i = new Intent(ACTION);
            i.setPackage(c.getPackageName());
            int flags = Build.VERSION.SDK_INT >= 31 ? PendingIntent.FLAG_MUTABLE : 0;
            um.requestPermission(d, PendingIntent.getBroadcast(c, 0, i, flags));
            latch.await(waitMs, TimeUnit.MILLISECONDS);
        } finally {
            try { c.unregisterReceiver(r); } catch (Exception ignore) { }
        }
        return um.hasPermission(d);
    }

    public static class Transport implements AdbClient.AdbTransport {
        private final UsbDeviceConnection conn;
        private final UsbInterface intf;
        private final UsbEndpoint in, out;

        public Transport(UsbDeviceConnection conn, UsbInterface intf) throws Exception {
            this.conn = conn;
            this.intf = intf;
            UsbEndpoint i = null, o = null;
            for (int k = 0; k < intf.getEndpointCount(); k++) {
                UsbEndpoint e = intf.getEndpoint(k);
                if (e.getType() != UsbConstants.USB_ENDPOINT_XFER_BULK) continue;
                if (e.getDirection() == UsbConstants.USB_DIR_IN) i = e; else o = e;
            }
            if (i == null || o == null) throw new Exception("no bulk endpoints");
            in = i;
            out = o;
            if (!conn.claimInterface(intf, true)) throw new Exception("claim failed");
        }

        @Override
        public void write(byte[] data, int len) throws Exception {
            int off = 0;
            while (off < len) {
                int n = Math.min(16384, len - off);
                int k = conn.bulkTransfer(out, data, off, n, 5000);
                if (k <= 0) throw new Exception("usb write failed");
                off += k;
            }
        }

        @Override
        public int read(byte[] buf, int len, int timeoutMs) throws Exception {
            int k = conn.bulkTransfer(in, buf, len, timeoutMs);
            if (k < 0) throw new Exception("usb read timeout");
            return k;
        }

        @Override
        public void close() {
            try { conn.releaseInterface(intf); } catch (Exception ignore) { }
            try { conn.close(); } catch (Exception ignore) { }
        }
    }
}
