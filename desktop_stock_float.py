from __future__ import annotations

import json
import queue
import re
import threading
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from tkinter import BOTH, BOTTOM, LEFT, RIGHT, TOP, Button, Canvas, Entry, Frame, Label, Menu, Scale, Scrollbar, StringVar, Tk, X, Y, messagebox


APP_DIR = Path(__file__).resolve().parent
CONFIG_PATH = APP_DIR / "stock_float_config.json"
DEFAULT_POOL = ["000021", "000725", "000938", "603099", "002745", "001309", "603986", "600667"]
DEFAULT_ALPHA = 0.56
POLL_INTERVAL_SECONDS = 1.0
HTTP_TIMEOUT_SECONDS = 3.0
WINDOW_WIDTH = 260
WINDOW_HEIGHT = 190
ROWS_VIEW_HEIGHT = 82
ERROR_RETRY_INTERVAL_SECONDS = 3.0
QUOTE_URL_TEMPLATES = [
    "https://hq.sinajs.cn/list={symbols}",
    "http://hq.sinajs.cn/list={symbols}",
]


@dataclass
class Quote:
    code: str
    name: str
    price: float | None
    change_pct: float | None
    open_price: float | None
    previous_close: float | None
    high: float | None
    low: float | None
    volume: int | None
    amount: float | None
    quote_time: str
    error: str = ""


def normalize_symbol(raw: str) -> str:
    symbol = re.sub(r"\D", "", raw.strip())
    if len(symbol) != 6:
        raise ValueError("请输入 6 位 A 股代码，例如 000725 或 600667")
    return symbol


def sina_market_symbol(symbol: str) -> str:
    if symbol.startswith(("6", "9")):
        return f"sh{symbol}"
    if symbol.startswith(("0", "2", "3")):
        return f"sz{symbol}"
    if symbol.startswith(("4", "8")):
        return f"bj{symbol}"
    raise ValueError(f"暂不支持该代码前缀: {symbol}")


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        return {"symbols": DEFAULT_POOL, "alpha": DEFAULT_ALPHA}
    try:
        config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"symbols": DEFAULT_POOL, "alpha": DEFAULT_ALPHA}
    symbols = []
    for item in config.get("symbols", DEFAULT_POOL):
        try:
            symbols.append(normalize_symbol(str(item)))
        except ValueError:
            continue
    return {
        "symbols": symbols or DEFAULT_POOL,
        "alpha": float(config.get("alpha", DEFAULT_ALPHA)),
    }


def save_config(symbols: list[str], alpha: float) -> None:
    CONFIG_PATH.write_text(
        json.dumps({"symbols": symbols, "alpha": alpha}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def fetch_sina_quotes(symbols: list[str]) -> dict[str, Quote]:
    if not symbols:
        return {}

    market_symbols = [sina_market_symbol(symbol) for symbol in symbols]
    encoded_list = ",".join(market_symbols)
    text = ""
    last_error: Exception | None = None
    for template in QUOTE_URL_TEMPLATES:
        url = template.format(symbols=urllib.parse.quote(encoded_list, safe=","))
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "*/*",
                "Referer": "https://finance.sina.com.cn/",
                "User-Agent": "Mozilla/5.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                text = response.read().decode("gbk", errors="ignore")
            if text.strip():
                break
        except Exception as exc:
            last_error = exc
    else:
        if last_error is not None:
            raise RuntimeError(f"行情接口请求失败: {last_error}") from last_error
        raise RuntimeError("行情接口返回为空")

    quotes: dict[str, Quote] = {}
    for line in text.splitlines():
        match = re.search(r"hq_str_(?:sh|sz|bj)(\d{6})=\"(.*)\";", line)
        if not match:
            continue
        code, payload = match.groups()
        parts = payload.split(",")
        if len(parts) < 32 or not parts[0]:
            quotes[code] = Quote(code, code, None, None, None, None, None, None, None, None, "", "无行情")
            continue

        name = parts[0].strip() or code
        open_price = to_float(parts[1])
        previous_close = to_float(parts[2])
        price = to_float(parts[3])
        high = to_float(parts[4])
        low = to_float(parts[5])
        volume = to_int(parts[8])
        amount = to_float(parts[9])
        quote_time = f"{parts[30]} {parts[31]}".strip()
        change_pct = None
        if price is not None and previous_close not in (None, 0):
            change_pct = (price - previous_close) / previous_close * 100
        quotes[code] = Quote(code, name, price, change_pct, open_price, previous_close, high, low, volume, amount, quote_time)
    if not quotes:
        raise RuntimeError("行情接口未返回可解析数据")
    return quotes


def to_float(value: str) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def to_int(value: str) -> int | None:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def format_price(value: float | None) -> str:
    return "--" if value is None else f"{value:.2f}"


def format_pct(value: float | None) -> str:
    if value is None:
        return "--"
    sign = "+" if value > 0 else ""
    return f"{sign}{value:.2f}%"


class StockFloatApp:
    def __init__(self) -> None:
        self.config = load_config()
        self.symbols: list[str] = list(dict.fromkeys(self.config["symbols"]))
        self.alpha = max(0.18, min(1.0, self.config["alpha"]))
        self.running = False
        self.fetching = False
        self.closed = False
        self.dragging = False
        self.drag_offset_x = 0
        self.drag_offset_y = 0
        self.result_queue: queue.Queue[tuple[dict[str, Quote] | None, str | None]] = queue.Queue()
        self.row_labels: dict[str, dict[str, Label]] = {}

        self.root = Tk()
        self.root.title("Stock Float")
        self.root.geometry(f"{WINDOW_WIDTH}x{WINDOW_HEIGHT}+60+80")
        self.root.resizable(False, False)
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.attributes("-alpha", self.alpha)
        self.root.configure(bg="#101418")

        self.status_var = StringVar(value="已暂停")
        self.input_var = StringVar()

        self.build_window()
        self.bind_window_events()
        self.render_rows()
        self.root.after(250, self.process_queue)

    def build_window(self) -> None:
        self.container = Frame(self.root, bg="#101418", padx=8, pady=6)
        self.container.pack(fill=BOTH, expand=True)

        top_bar = Frame(self.container, bg="#101418")
        top_bar.pack(side=TOP, fill=X)

        self.start_button = Button(top_bar, text="▶", width=3, command=self.toggle_running, bg="#1f2933", fg="#d8dee9", relief="flat")
        self.start_button.pack(side=LEFT, padx=(0, 4))

        self.status_label = Label(top_bar, textvariable=self.status_var, bg="#101418", fg="#9aa4af", font=("Microsoft YaHei UI", 8))
        self.status_label.pack(side=LEFT)

        self.close_button = Button(top_bar, text="×", width=3, command=self.close, bg="#101418", fg="#9aa4af", relief="flat")
        self.close_button.pack(side=RIGHT)

        rows_view = Frame(self.container, width=WINDOW_WIDTH - 16, height=ROWS_VIEW_HEIGHT, bg="#101418")
        rows_view.pack(side=TOP, fill=X, pady=(4, 4))
        rows_view.pack_propagate(False)

        self.rows_canvas = Canvas(rows_view, bg="#101418", highlightthickness=0, bd=0)
        self.rows_canvas.pack(side=LEFT, fill=BOTH, expand=True)

        self.rows_scrollbar = Scrollbar(rows_view, orient="vertical", command=self.rows_canvas.yview, width=8)
        self.rows_scrollbar.pack(side=RIGHT, fill=Y)
        self.rows_canvas.configure(yscrollcommand=self.rows_scrollbar.set)

        self.rows_frame = Frame(self.rows_canvas, bg="#101418")
        self.rows_window_id = self.rows_canvas.create_window((0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.bind("<Configure>", self.update_scroll_region)
        self.rows_canvas.bind("<Configure>", self.resize_rows_window)

        control = Frame(self.container, bg="#101418")
        control.pack(side=BOTTOM, fill=X)

        self.symbol_entry = Entry(control, textvariable=self.input_var, width=10, bg="#0b0f14", fg="#d8dee9", insertbackground="#d8dee9", relief="flat")
        self.symbol_entry.pack(side=LEFT, fill=X, expand=True, padx=(0, 4))
        self.symbol_entry.bind("<Return>", lambda _event: self.add_symbol())

        Button(control, text="+", width=3, command=self.add_symbol, bg="#1f2933", fg="#d8dee9", relief="flat").pack(side=LEFT, padx=(0, 3))
        Button(control, text="-", width=3, command=self.remove_last_symbol, bg="#1f2933", fg="#d8dee9", relief="flat").pack(side=LEFT)

        opacity = Scale(
            self.container,
            from_=18,
            to=100,
            orient="horizontal",
            showvalue=False,
            length=160,
            command=self.update_alpha,
            bg="#101418",
            fg="#9aa4af",
            troughcolor="#26313d",
            highlightthickness=0,
            relief="flat",
        )
        opacity.set(int(self.alpha * 100))
        opacity.pack(side=BOTTOM, fill=X, pady=(4, 0))

        self.context_menu = Menu(self.root, tearoff=0)
        self.context_menu.add_command(label="开始/暂停", command=self.toggle_running)
        self.context_menu.add_command(label="刷新一次", command=self.fetch_once)
        self.context_menu.add_separator()
        self.context_menu.add_command(label="关闭浮窗", command=self.close)

    def bind_window_events(self) -> None:
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.bind_all("<ButtonPress-1>", self.start_drag)
        self.root.bind_all("<B1-Motion>", self.drag)
        self.root.bind_all("<ButtonRelease-1>", self.stop_drag)
        self.root.bind("<Button-3>", self.show_context_menu)
        self.root.bind("<Escape>", lambda _event: self.close())
        self.root.bind_all("<MouseWheel>", self.on_mousewheel)

    def start_drag(self, event) -> None:
        if self.is_control_widget(event.widget):
            self.dragging = False
            return
        self.dragging = True
        self.drag_offset_x = event.x_root - self.root.winfo_x()
        self.drag_offset_y = event.y_root - self.root.winfo_y()

    def drag(self, event) -> None:
        if not self.dragging:
            return
        x = event.x_root - self.drag_offset_x
        y = event.y_root - self.drag_offset_y
        self.root.geometry(f"+{x}+{y}")

    def stop_drag(self, _event) -> None:
        self.dragging = False

    def is_control_widget(self, widget) -> bool:
        return widget.winfo_class() in {"Button", "Entry", "Scale", "Scrollbar"}

    def show_context_menu(self, event) -> None:
        self.context_menu.tk_popup(event.x_root, event.y_root)

    def update_scroll_region(self, _event=None) -> None:
        self.rows_canvas.configure(scrollregion=self.rows_canvas.bbox("all"))

    def resize_rows_window(self, event) -> None:
        self.rows_canvas.itemconfigure(self.rows_window_id, width=event.width)

    def on_mousewheel(self, event) -> None:
        self.rows_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    def render_rows(self, quotes: dict[str, Quote] | None = None) -> None:
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self.row_labels.clear()

        for symbol in self.symbols:
            quote = quotes.get(symbol) if quotes else None
            row = Frame(self.rows_frame, bg="#101418")
            row.pack(side=TOP, fill=X, pady=1)

            name_text = quote.name if quote else symbol
            code_label = Label(row, text=f"{name_text} {symbol}", width=16, anchor="w", bg="#101418", fg="#c9d1d9", font=("Microsoft YaHei UI", 8))
            code_label.pack(side=LEFT)

            price_label = Label(row, text=format_price(quote.price if quote else None), width=7, anchor="e", bg="#101418", fg="#d8dee9", font=("Consolas", 9))
            price_label.pack(side=LEFT)

            pct_value = quote.change_pct if quote else None
            pct_color = "#ff5c5c" if pct_value and pct_value > 0 else "#3fb950" if pct_value and pct_value < 0 else "#9aa4af"
            pct_label = Label(row, text=format_pct(pct_value), width=8, anchor="e", bg="#101418", fg=pct_color, font=("Consolas", 9))
            pct_label.pack(side=LEFT)

            row.bind("<Double-Button-1>", lambda _event, s=symbol: self.remove_symbol(s))
            code_label.bind("<Double-Button-1>", lambda _event, s=symbol: self.remove_symbol(s))
            price_label.bind("<Double-Button-1>", lambda _event, s=symbol: self.remove_symbol(s))
            pct_label.bind("<Double-Button-1>", lambda _event, s=symbol: self.remove_symbol(s))

            self.row_labels[symbol] = {"code": code_label, "price": price_label, "pct": pct_label}
        self.update_scroll_region()

    def update_rows(self, quotes: dict[str, Quote]) -> None:
        for symbol in self.symbols:
            quote = quotes.get(symbol)
            if not quote or symbol not in self.row_labels:
                continue
            labels = self.row_labels[symbol]
            labels["code"].configure(text=f"{quote.name} {symbol}" if not quote.error else f"{symbol} {quote.error}")
            labels["price"].configure(text=format_price(quote.price))
            labels["pct"].configure(text=format_pct(quote.change_pct))
            if quote.change_pct is None:
                color = "#9aa4af"
            elif quote.change_pct > 0:
                color = "#ff5c5c"
            elif quote.change_pct < 0:
                color = "#3fb950"
            else:
                color = "#9aa4af"
            labels["pct"].configure(fg=color)

    def toggle_running(self) -> None:
        self.running = not self.running
        self.start_button.configure(text="Ⅱ" if self.running else "▶")
        self.status_var.set("获取中" if self.running else "已暂停")
        if self.running:
            self.fetch_once()

    def fetch_once(self) -> None:
        if self.fetching or not self.symbols:
            return
        self.fetching = True
        symbols_snapshot = list(self.symbols)
        thread = threading.Thread(target=self.worker_fetch, args=(symbols_snapshot,), daemon=True)
        thread.start()

    def worker_fetch(self, symbols_snapshot: list[str]) -> None:
        try:
            quotes = fetch_sina_quotes(symbols_snapshot)
            self.result_queue.put((quotes, None))
        except Exception as exc:
            self.result_queue.put((None, str(exc)))

    def process_queue(self) -> None:
        try:
            while True:
                quotes, error = self.result_queue.get_nowait()
                self.fetching = False
                if error:
                    brief_error = error.splitlines()[-1][:18]
                    self.status_var.set(f"失败 {time.strftime('%H:%M:%S')} {brief_error}")
                elif quotes is not None:
                    self.update_rows(quotes)
                    self.status_var.set(f"更新 {time.strftime('%H:%M:%S')}" if self.running else "已刷新")
                if self.running and not self.closed:
                    interval = ERROR_RETRY_INTERVAL_SECONDS if error else POLL_INTERVAL_SECONDS
                    self.root.after(int(interval * 1000), self.fetch_once)
        except queue.Empty:
            pass

        if not self.closed:
            self.root.after(250, self.process_queue)

    def add_symbol(self) -> None:
        try:
            symbol = normalize_symbol(self.input_var.get())
        except ValueError as exc:
            messagebox.showwarning("股票代码", str(exc))
            return
        if symbol not in self.symbols:
            self.symbols.append(symbol)
            self.input_var.set("")
            save_config(self.symbols, self.alpha)
            self.render_rows()
            if self.running:
                self.fetch_once()
        else:
            self.input_var.set("")

    def remove_last_symbol(self) -> None:
        if not self.symbols:
            return
        self.remove_symbol(self.symbols[-1])

    def remove_symbol(self, symbol: str) -> None:
        if symbol in self.symbols:
            self.symbols.remove(symbol)
            save_config(self.symbols, self.alpha)
            self.render_rows()

    def update_alpha(self, value: str) -> None:
        self.alpha = max(0.18, min(1.0, float(value) / 100))
        self.root.attributes("-alpha", self.alpha)
        save_config(self.symbols, self.alpha)

    def close(self) -> None:
        self.closed = True
        save_config(self.symbols, self.alpha)
        self.root.destroy()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    StockFloatApp().run()
