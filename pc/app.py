# -*- coding: utf-8 -*-
"""واجهة فاحص الأجهزة (EXE) — tkinter."""
import datetime
import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import core

STATUS_TXT = {core.OK: "✔ سليم", core.WARN: "⚠ تحذير", core.BAD: "✖ عطل", core.INFO: "ℹ معلومة"}
BG = {core.OK: "#e6f4ea", core.WARN: "#fff4ce", core.BAD: "#fde7e9", core.INFO: "#eef3fb"}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("فاحص الأجهزة — Device Doctor")
        self.geometry("1080x700")
        self.minsize(860, 540)
        self.results, self.by_iid = [], {}
        self.q, self.busy = queue.Queue(), False
        self._build()
        self.after(100, self._poll)
        if core.IS_WIN and not core.is_admin():
            self.status.set("تنبيه: شغّل البرنامج كمسؤول (Run as administrator) لنتائج أدق للأقراص والحرارة.")

    def _build(self):
        st = ttk.Style(self)
        st.configure("Treeview", rowheight=30, font=("Segoe UI", 10))
        st.configure("Treeview.Heading", font=("Segoe UI", 10, "bold"))
        top = ttk.Frame(self, padding=8)
        top.pack(fill="x")
        self.deep = tk.BooleanVar(value=False)
        self.btns = [
            ttk.Button(top, text="🖥  فحص الكمبيوتر", command=self.scan_pc),
            ttk.Button(top, text="📱  فحص الموبايل (USB)", command=self.scan_phone),
            ttk.Button(top, text="🔍  فحص جهاز آخر", command=self.remote_dialog),
            ttk.Button(top, text="💽  فحص قرص جهاز معطّل", command=self.offline_dialog),
            ttk.Button(top, text="💾  حفظ التقرير", command=self.save),
            ttk.Button(top, text="🗑  مسح", command=self.clear),
        ]
        ttk.Checkbutton(top, text="فحص عميق (2–4 دقائق)", variable=self.deep).pack(side="right", padx=8)
        self.btn_srv = ttk.Button(top, text="📡  وضع الاستقبال", command=self.toggle_server)
        self.btn_srv.pack(side="right", padx=4)
        self.server = None
        for b in self.btns:
            b.pack(side="right", padx=4)
        self.status = tk.StringVar(value="جاهز. اختر نوع الفحص.")
        ttk.Label(top, textvariable=self.status).pack(side="left", padx=6)
        self.pb = ttk.Progressbar(self, mode="indeterminate")
        self.pb.pack(fill="x", padx=8)
        self.summary = tk.StringVar()
        ttk.Label(self, textvariable=self.summary, font=("Segoe UI", 11, "bold")).pack(anchor="e", padx=12, pady=4)

        mid = ttk.Frame(self)
        mid.pack(fill="both", expand=True, padx=8)
        cols = ("details", "name", "group", "status")
        self.tree = ttk.Treeview(mid, columns=cols, show="headings", selectmode="browse")
        for c, t, w in (("details", "التفاصيل", 460), ("name", "الفحص", 230), ("group", "القسم", 90), ("status", "الحالة", 100)):
            self.tree.heading(c, text=t, anchor="e")
            self.tree.column(c, width=w, anchor="e")
        for k, v in BG.items():
            self.tree.tag_configure(k, background=v)
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side="left", fill="y")
        self.tree.pack(side="right", fill="both", expand=True)
        self.tree.bind("<<TreeviewSelect>>", self._show)

        self.detail = tk.Text(self, height=6, wrap="word", font=("Segoe UI", 11), state="disabled")
        self.detail.tag_configure("r", justify="right")
        self.detail.pack(fill="x", padx=8, pady=8)
        self._count()

    def toggle_server(self):
        if self.server:
            self.server.stop()
            self.server = None
            self.btn_srv.configure(text="📡  وضع الاستقبال")
            self.status.set("تم إيقاف وضع الاستقبال.")
            return
        try:
            srv = core.RemoteServer(8765, scan_fn=lambda deep=False: core.run_all_pc(deep))
            srv.start()
        except OSError as e:
            messagebox.showerror("وضع الاستقبال", f"تعذّر فتح المنفذ 8765: {e}")
            return
        self.server = srv
        self.btn_srv.configure(text="⏹  إيقاف الاستقبال")
        ip = core.local_ip()
        self.status.set(f"جاهز للموبايل — IP: {ip}   PIN: {srv.pin}")
        messagebox.showinfo("وضع الاستقبال",
                            f"من تطبيق الموبايل (تبويب «جهاز آخر») أو من DeviceDoctor على كمبيوتر ثاني (زر «فحص جهاز آخر») اكتب:\n\nIP: {ip}\nPIN: {srv.pin}\n\n"
                            "لازم الاثنين على نفس الشبكة. " + ("فتحت قاعدة Firewall للمنفذ 8765 وتنغلق عند الإيقاف." if srv.fw else "إذا ما اتصل: اسمح للبرنامج بالـFirewall (شبكة خاصة)."))


    # ───────── فحص جهاز آخر (كمبيوتر أو موبايل بوضع الاستقبال) ─────────
    def remote_dialog(self):
        if self.busy:
            return
        w = tk.Toplevel(self)
        w.title("فحص جهاز آخر")
        w.geometry("470x370")
        w.transient(self)
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, text="على الجهاز الثاني شغّل «وضع الاستقبال» (كمبيوتر: DeviceDoctor، موبايل: التطبيق)،\nثم اكتب IP وPIN اللي يظهرون هناك أو اضغط بحث تلقائي.", justify="right").pack(anchor="e")
        ip_v, pin_v, deep_v = tk.StringVar(), tk.StringVar(), tk.BooleanVar(value=self.deep.get())
        ttk.Label(f, text="IP").pack(anchor="e", pady=(10, 0))
        ttk.Entry(f, textvariable=ip_v, justify="left").pack(fill="x")
        ttk.Label(f, text="PIN").pack(anchor="e", pady=(6, 0))
        ttk.Entry(f, textvariable=pin_v, justify="left").pack(fill="x")
        ttk.Checkbutton(f, text="فحص عميق (للكمبيوتر فقط)", variable=deep_v).pack(anchor="e", pady=6)
        st = tk.StringVar()
        ttk.Label(f, textvariable=st, foreground="#555").pack(anchor="e")

        def find():
            st.set("جاري البحث على الشبكة ...")

            def work():
                found = core.discover_lan()
                self.after(0, lambda: done(found))

            def done(found):
                if found:
                    ip_v.set(found[0][0])
                    st.set("لقيت: " + "، ".join(f"{i} ({(p or {}).get('name', '')})" for i, p in found))
                else:
                    st.set("ما لقيت جهاز. تأكد من وضع الاستقبال والشبكة.")
            threading.Thread(target=work, daemon=True).start()

        def go():
            ip, pin = ip_v.get().strip(), pin_v.get().strip()
            if not ip or not pin:
                st.set("اكتب IP وPIN.")
                return
            deep = deep_v.get()
            w.destroy()

            def work():
                self.q.put(("status", f"جاري فحص {ip} ... (قد يستغرق دقائق)"))
                try:
                    info, res = core.remote_scan(ip, pin, deep)
                    for r in res:
                        self.q.put(("row", r))
                except (ConnectionError, PermissionError) as e:
                    self.q.put(("row", core.Result("جهاز آخر", f"فحص {ip}", core.BAD, str(e))))
                except Exception as e:
                    self.q.put(("row", core.Result("جهاز آخر", f"فحص {ip}", core.BAD, f"فشل: {e}")))
                self.q.put(("done", None))
            self._start(work)
        adapters = {}

        def direct():
            rows = core.list_adapters()
            if not rows:
                st.set("ما لقيت كرت شبكة سلكي شغّال. وصّل الكيبل بين الجهازين وتأكد أن الثاني مشغّل.")
                return
            nm = rows[0].get("Name")
            adapters["last"] = nm
            if core.set_static_ip(nm):
                ip_v.set(core.DIRECT_PEER)
                st.set(f"جاهز على {nm}: عنوانك {core.DIRECT_ME}. الجهاز المعطّل لازم يكون {core.DIRECT_PEER} (الفلاشة تضبطه تلقائيًا).")
            else:
                st.set("تعذّر ضبط عنوان الكرت (شغّل البرنامج كمسؤول).")

        def undo():
            nm = adapters.get("last") or (core.list_adapters() or [{}])[0].get("Name")
            st.set("رجّعت الكرت لـDHCP." if nm and core.restore_dhcp(nm) else "تعذّر الإرجاع؛ من إعدادات الشبكة اختر «الحصول تلقائيًا».")
        r = ttk.Frame(f)
        r.pack(fill="x", pady=8)
        r2 = ttk.Frame(f)
        r2.pack(fill="x")
        ttk.Button(r2, text="🔌 تجهيز كيبل مباشر", command=direct).pack(side="right", padx=4)
        ttk.Button(r2, text="↩ إرجاع الشبكة", command=undo).pack(side="right", padx=4)
        ttk.Button(r, text="🔎 بحث تلقائي", command=find).pack(side="right", padx=4)
        ttk.Button(r, text="▶ افحص", command=go).pack(side="right", padx=4)

    # ───────── فحص قرص جهاز معطّل (قرصه موصول بهذا الكمبيوتر) ─────────
    def offline_dialog(self):
        if self.busy:
            return
        w = tk.Toplevel(self)
        w.title("فحص قرص جهاز معطّل")
        w.geometry("520x330")
        w.transient(self)
        f = ttk.Frame(w, padding=12)
        f.pack(fill="both", expand=True)
        ttk.Label(f, justify="right", text="طلّع قرص الجهاز المعطّل ووصّله بهذا الكمبيوتر (علبة USB أو SATA).\n"
                  "إذا ويندوز سأل «تهيئة القرص» اضغط إلغاء؛ لا تهيّئه.\n"
                  "القرص المشفّر بـBitLocker لازم تفتحه أولًا بمفتاح الاسترداد.").pack(anchor="e")
        var = tk.StringVar()
        cb = ttk.Combobox(f, textvariable=var, state="readonly", justify="left")
        cb.pack(fill="x", pady=8)
        st = tk.StringVar()
        ttk.Label(f, textvariable=st, foreground="#555").pack(anchor="e")
        deep_v = tk.BooleanVar(value=self.deep.get())
        ttk.Checkbutton(f, text="فحص عميق (chkdsk للقسم، قد ياخذ دقائق طويلة)", variable=deep_v).pack(anchor="e", pady=4)

        def refresh():
            roots = core.find_offline_windows()
            cb["values"] = roots
            if roots:
                var.set(roots[0])
                st.set(f"لقيت {len(roots)} قسم فيه ويندوز.")
            else:
                st.set("ما لقيت ويندوز على أي قرص موصول. وصّل القرص واضغط «تحديث» (أو القسم مشفّر/تالف).")

        def go():
            root = var.get()
            if not root:
                st.set("اختر القسم أولًا.")
                return
            deep = deep_v.get()
            w.destroy()

            def work():
                self.q.put(("status", f"جاري فحص ويندوز على {root} ..."))
                for r in core.check_offline_windows(root, deep):
                    self.q.put(("row", r))
                self.q.put(("done", None))
            self._start(work)
        r = ttk.Frame(f)
        r.pack(fill="x", pady=8)
        ttk.Button(r, text="🔄 تحديث", command=refresh).pack(side="right", padx=4)
        ttk.Button(r, text="▶ افحص", command=go).pack(side="right", padx=4)
        refresh()

    def _buttons(self, on):
        for b in self.btns[:4]:
            b.configure(state="normal" if on else "disabled")

    def _start(self, work):
        if self.busy:
            return
        self.busy = True
        self._buttons(False)
        self.pb.start(12)
        threading.Thread(target=work, daemon=True).start()

    def scan_pc(self):
        deep = self.deep.get()

        def work():
            if not core.IS_WIN:
                self.q.put(("row", core.Result("الكمبيوتر", "فحص الكمبيوتر", core.INFO, "فحص الكمبيوتر يعمل على ويندوز فقط")))
            else:
                for label, fn in core.pc_checks(deep):
                    self.q.put(("status", f"جاري فحص: {label} ..."))
                    for r in core.safe("الكمبيوتر", label, fn):
                        self.q.put(("row", r))
            self.q.put(("done", None))
        self._start(work)

    def scan_phone(self):
        def work():
            self.q.put(("status", "جاري البحث عن الموبايل ..."))
            adb, early = core.phone_prepare()
            for r in early:
                self.q.put(("row", r))
            if adb:
                for label, fn in core.phone_checks(adb):
                    self.q.put(("status", f"جاري فحص الموبايل: {label} ..."))
                    for r in core.safe("الموبايل", label, fn):
                        self.q.put(("row", r))
            self.q.put(("done", None))
        self._start(work)

    def _poll(self):
        try:
            while True:
                kind, val = self.q.get_nowait()
                if kind == "status":
                    self.status.set(val)
                elif kind == "row":
                    self._add(val)
                elif kind == "done":
                    self.busy = False
                    self.pb.stop()
                    self._buttons(True)
                    self.status.set("تم الفحص. اضغط على أي سطر لرؤية الحل المقترح.")
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _add(self, r):
        self.results.append(r)
        iid = self.tree.insert("", "end", values=(r.details, r.name, r.group, STATUS_TXT[r.status]), tags=(r.status,))
        self.by_iid[iid] = r
        self._count()

    def _count(self):
        c = {k: sum(1 for r in self.results if r.status == k) for k in STATUS_TXT}
        self.summary.set(f"سليم: {c[core.OK]}    تحذير: {c[core.WARN]}    عطل: {c[core.BAD]}")

    def _show(self, _e=None):
        sel = self.tree.selection()
        if not sel:
            return
        r = self.by_iid[sel[0]]
        txt = f"{r.name}  —  {STATUS_TXT[r.status]}\n{r.details}\n" + (f"\nالحل المقترح: {r.fix}" if r.fix else "")
        self.detail.configure(state="normal")
        self.detail.delete("1.0", "end")
        self.detail.insert("1.0", txt, "r")
        self.detail.configure(state="disabled")

    def clear(self):
        if self.busy:
            return
        self.tree.delete(*self.tree.get_children())
        self.results, self.by_iid = [], {}
        self._count()

    def save(self):
        if not self.results:
            messagebox.showinfo("تقرير", "ما فيه نتائج للحفظ. اعمل فحص أول.")
            return
        name = "DeviceDoctor-" + datetime.datetime.now().strftime("%Y%m%d-%H%M") + ".html"
        path = filedialog.asksaveasfilename(defaultextension=".html", initialfile=name, filetypes=[("HTML", "*.html")])
        if path:
            with open(path, "w", encoding="utf-8") as f:
                f.write(core.make_html(self.results))
            messagebox.showinfo("تقرير", "تم حفظ التقرير.")


if __name__ == "__main__":
    App().mainloop()
