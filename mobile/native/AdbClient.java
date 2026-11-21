package com.devicedoctor.app;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.math.BigInteger;
import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.interfaces.RSAPublicKey;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;

/**
 * عميل ADB بسيط (بروتوكول USB/TCP): اتصال + مصادقة RSA + تنفيذ أوامر shell.
 * مفصول عن أندرويد عبر واجهة AdbTransport حتى يمكن اختباره بشكل مستقل.
 */
public class AdbClient {

    public interface AdbTransport {
        /** يكتب كل البايتات أو يرمي استثناء. */
        void write(byte[] data, int len) throws Exception;

        /** يقرأ حتى len بايت (على الأقل بايت واحد) أو يرمي استثناء عند انتهاء المهلة. */
        int read(byte[] buf, int len, int timeoutMs) throws Exception;

        void close();
    }

    static final int A_SYNC = 0x434e5953, A_CNXN = 0x4e584e43, A_AUTH = 0x48545541;
    static final int A_OPEN = 0x4e45504f, A_OKAY = 0x59414b4f, A_CLSE = 0x45534c43, A_WRTE = 0x45545257;
    static final int VERSION = 0x01000000, MAXDATA = 4096;
    static final int AUTH_TOKEN = 1, AUTH_SIGNATURE = 2, AUTH_RSAPUBLICKEY = 3;
    // DigestInfo لـ SHA-1 (يُستخدم لأن الـtoken هو أصلًا hash)
    static final byte[] SHA1_PREFIX = {0x30, 0x21, 0x30, 0x09, 0x06, 0x05, 0x2b, 0x0e, 0x03, 0x02, 0x1a, 0x05, 0x00, 0x04, 0x14};

    private final AdbTransport tr;
    private final KeyPair keys;
    private int maxData = MAXDATA;
    private int nextLocal = 1;
    public String deviceBanner = "";

    public AdbClient(AdbTransport tr, KeyPair keys) {
        this.tr = tr;
        this.keys = keys;
    }

    // ───────────── المفاتيح ─────────────
    public static KeyPair loadOrCreateKeys(File dir) throws Exception {
        File priv = new File(dir, "adbkey.pk8"), pub = new File(dir, "adbkey.x509");
        if (priv.exists() && pub.exists()) {
            try {
                KeyFactory kf = KeyFactory.getInstance("RSA");
                PrivateKey pr = kf.generatePrivate(new PKCS8EncodedKeySpec(readAll(priv)));
                java.security.PublicKey pu = kf.generatePublic(new X509EncodedKeySpec(readAll(pub)));
                return new KeyPair(pu, pr);
            } catch (Exception ignored) {
                // نولّد جديدًا
            }
        }
        KeyPairGenerator g = KeyPairGenerator.getInstance("RSA");
        g.initialize(2048);
        KeyPair kp = g.generateKeyPair();
        writeAll(priv, kp.getPrivate().getEncoded());
        writeAll(pub, kp.getPublic().getEncoded());
        return kp;
    }

    private static byte[] readAll(File f) throws Exception {
        FileInputStream in = new FileInputStream(f);
        ByteArrayOutputStream o = new ByteArrayOutputStream();
        byte[] b = new byte[4096];
        int k;
        while ((k = in.read(b)) > 0) o.write(b, 0, k);
        in.close();
        return o.toByteArray();
    }

    private static void writeAll(File f, byte[] d) throws Exception {
        FileOutputStream o = new FileOutputStream(f);
        o.write(d);
        o.close();
    }

    /** المفتاح العام بصيغة أندرويد (RSAPublicKey struct) مشفّر Base64 + اسم + NUL. */
    public static byte[] androidPublicKey(RSAPublicKey k, String name) {
        final int words = 64;
        BigInteger n = k.getModulus();
        BigInteger r32 = BigInteger.ONE.shiftLeft(32);
        long n0inv = n.mod(r32).modInverse(r32).negate().mod(r32).longValue();
        BigInteger rr = BigInteger.ONE.shiftLeft(2048 * 2).mod(n);
        ByteBuffer bb = ByteBuffer.allocate(4 + 4 + 4 * words + 4 * words + 4).order(ByteOrder.LITTLE_ENDIAN);
        bb.putInt(words);
        bb.putInt((int) n0inv);
        putLE(bb, n, words * 4);
        putLE(bb, rr, words * 4);
        bb.putInt(k.getPublicExponent().intValue());
        String b64 = b64(bb.array());
        byte[] tail = (b64 + " " + name).getBytes();
        byte[] out = new byte[tail.length + 1];
        System.arraycopy(tail, 0, out, 0, tail.length);
        return out;
    }

    private static void putLE(ByteBuffer bb, BigInteger v, int len) {
        byte[] be = v.toByteArray();
        byte[] le = new byte[len];
        for (int i = 0; i < len; i++) {
            int src = be.length - 1 - i;
            le[i] = src >= 0 ? be[src] : 0;
        }
        bb.put(le);
    }

    private static String b64(byte[] d) {
        final String T = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/";
        StringBuilder sb = new StringBuilder();
        for (int i = 0; i < d.length; i += 3) {
            int b0 = d[i] & 0xff, b1 = i + 1 < d.length ? d[i + 1] & 0xff : 0, b2 = i + 2 < d.length ? d[i + 2] & 0xff : 0;
            sb.append(T.charAt(b0 >> 2)).append(T.charAt(((b0 & 3) << 4) | (b1 >> 4)));
            sb.append(i + 1 < d.length ? T.charAt(((b1 & 15) << 2) | (b2 >> 6)) : '=');
            sb.append(i + 2 < d.length ? T.charAt(b2 & 63) : '=');
        }
        return sb.toString();
    }

    // ───────────── الرسائل ─────────────
    private static int sum(byte[] d, int len) {
        int s = 0;
        for (int i = 0; i < len; i++) s += d[i] & 0xff;
        return s;
    }

    private void send(int cmd, int a0, int a1, byte[] data) throws Exception {
        int len = data == null ? 0 : data.length;
        ByteBuffer h = ByteBuffer.allocate(24).order(ByteOrder.LITTLE_ENDIAN);
        h.putInt(cmd).putInt(a0).putInt(a1).putInt(len).putInt(data == null ? 0 : sum(data, len)).putInt(cmd ^ 0xffffffff);
        tr.write(h.array(), 24);
        if (len > 0) tr.write(data, len);
    }

    private void readFully(byte[] b, int len, int timeout) throws Exception {
        int got = 0;
        while (got < len) {
            byte[] tmp = new byte[len - got];
            int k = tr.read(tmp, tmp.length, timeout);
            if (k <= 0) throw new Exception("read timeout");
            System.arraycopy(tmp, 0, b, got, k);
            got += k;
        }
    }

    private static final class Msg {
        int cmd, a0, a1;
        byte[] data;
    }

    private Msg recv(int timeout) throws Exception {
        byte[] h = new byte[24];
        readFully(h, 24, timeout);
        ByteBuffer b = ByteBuffer.wrap(h).order(ByteOrder.LITTLE_ENDIAN);
        Msg m = new Msg();
        m.cmd = b.getInt();
        m.a0 = b.getInt();
        m.a1 = b.getInt();
        int len = b.getInt();
        b.getInt();
        int magic = b.getInt();
        if ((m.cmd ^ 0xffffffff) != magic) throw new Exception("bad adb magic");
        if (len < 0 || len > 1 << 20) throw new Exception("bad adb length");
        m.data = new byte[len];
        if (len > 0) readFully(m.data, len, timeout);
        return m;
    }

    // ───────────── الاتصال والمصادقة ─────────────
    /** توقيع بالمفتاح المحفوظ، ثم إرسال المفتاح العام وانتظار ضغط «سماح» (authWaitMs) على الجهاز الآخر. */
    public void connect(int authWaitMs) throws Exception {
        send(A_CNXN, VERSION, MAXDATA, "host::DeviceDoctor\0".getBytes());
        int tokens = 0;
        while (true) {
            Msg m = recv(tokens >= 2 ? authWaitMs : 8000);
            if (m.cmd == A_CNXN) {
                maxData = Math.max(256, Math.min(m.a1, 65536));
                deviceBanner = new String(m.data).trim();
                return;
            }
            if (m.cmd == A_AUTH && m.a0 == AUTH_TOKEN) {
                tokens++;
                if (tokens == 1) {
                    Signature s = Signature.getInstance("NONEwithRSA");
                    s.initSign(keys.getPrivate());
                    byte[] in = new byte[SHA1_PREFIX.length + m.data.length];
                    System.arraycopy(SHA1_PREFIX, 0, in, 0, SHA1_PREFIX.length);
                    System.arraycopy(m.data, 0, in, SHA1_PREFIX.length, m.data.length);
                    s.update(in);
                    send(A_AUTH, AUTH_SIGNATURE, 0, s.sign());
                } else if (tokens == 2) {
                    send(A_AUTH, AUTH_RSAPUBLICKEY, 0, androidPublicKey((RSAPublicKey) keys.getPublic(), "devicedoctor@phone"));
                } else {
                    throw new Exception("auth rejected");
                }
                continue;
            }
            throw new Exception("unexpected adb message");
        }
    }

    // ───────────── تنفيذ أمر shell ─────────────
    public String shell(String cmd, int timeoutMs) throws Exception {
        int local = nextLocal++;
        send(A_OPEN, local, 0, ("shell:" + cmd + "\0").getBytes());
        ByteArrayOutputStream out = new ByteArrayOutputStream();
        int remote = 0;
        long end = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() < end) {
            Msg m = recv((int) Math.max(1000, end - System.currentTimeMillis()));
            if (m.cmd == A_OKAY && m.a1 == local) {
                remote = m.a0;
            } else if (m.cmd == A_WRTE && m.a1 == local) {
                remote = m.a0;
                out.write(m.data, 0, m.data.length);
                send(A_OKAY, local, remote, null);
            } else if (m.cmd == A_CLSE && m.a1 == local) {
                if (remote == 0) remote = m.a0;
                send(A_CLSE, local, remote, null);
                return out.toString("UTF-8");
            }
            // رسائل متعلقة بقنوات أخرى: نتجاهلها
        }
        throw new Exception("shell timeout: " + cmd);
    }

    public void close() {
        tr.close();
    }
}
