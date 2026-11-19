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
        self.btns = [
            ttk.Button(top, text="🖥  فحص الكمبيوتر", command=self.scan_pc),
            ttk.Button(top, text="📱  فحص الموبايل (USB)", command=self.scan_phone),
            ttk.Button(top, text="💾  حفظ التقرير", command=self.save),
            ttk.Button(top, text="🗑  مسح", command=self.clear),
        ]
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

    def _buttons(self, on):
        for b in self.btns[:2]:
            b.configure(state="normal" if on else "disabled")

    def _start(self, work):
        if self.busy:
            return
        self.busy = True
        self._buttons(False)
        self.pb.start(12)
        threading.Thread(target=work, daemon=True).start()

    def scan_pc(self):
        def work():
            if not core.IS_WIN:
                self.q.put(("row", core.Result("الكمبيوتر", "فحص الكمبيوتر", core.INFO, "فحص الكمبيوتر يعمل على ويندوز فقط")))
            else:
                for label, fn in core.pc_checks():
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
