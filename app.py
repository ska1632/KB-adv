import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import gspread
from google.oauth2.service_account import Credentials

# --- 定数と設定 ---
DEFAULT_STOCKS = ["7203.T", "9984.T", "6758.T", "7974.T", "6501.T", "5803.T", "7984.T", "7453.T"]

# ページ設定
st.set_page_config(page_title="Professional Stock Monitor", layout="wide")
st.title("📈 株式クロス監視 ＆ プロ仕様複合アドバイザーダッシュボード")

# --- Google Sheets 接続 ---
@st.cache_resource
def get_gsheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_info(
        st.secrets["gcp_service_account"], scopes=scopes
    )
    client = gspread.authorize(creds)
    spreadsheet = client.open_by_key(st.secrets["SPREADSHEET_ID"])
    return spreadsheet

def get_worksheet(spreadsheet, sheet_name):
    try:
        return spreadsheet.worksheet(sheet_name)
    except gspread.exceptions.WorksheetNotFound:
        return spreadsheet.add_worksheet(title=sheet_name, rows=200, cols=10)

# --- Google Sheets 読み書き関数 ---
def load_stocks():
    try:
        spreadsheet = get_gsheet()
        ws = get_worksheet(spreadsheet, "stocks")
        values = ws.col_values(1)
        return [v for v in values if v] if values else DEFAULT_STOCKS
    except Exception:
        return DEFAULT_STOCKS

def save_stocks(stock_list):
    try:
        spreadsheet = get_gsheet()
        ws = get_worksheet(spreadsheet, "stocks")
        ws.clear()
        ws.update(range_name="A1", values=[[s] for s in stock_list])
    except Exception as e:
        st.error(f"銘柄リストの保存に失敗しました: {e}")

def load_portfolio():
    try:
        spreadsheet = get_gsheet()
        ws = get_worksheet(spreadsheet, "portfolio")
        records = ws.get_all_records()
        portfolio = {}
        for row in records:
            if row.get("symbol"):
                portfolio[row["symbol"]] = {
                    "price": float(row.get("price", 0)),
                    "qty":   int(row.get("qty", 0)),
                }
        return portfolio
    except Exception:
        return {}

def save_portfolio(portfolio):
    try:
        spreadsheet = get_gsheet()
        ws = get_worksheet(spreadsheet, "portfolio")
        ws.clear()
        ws.update(range_name="A1", values=[["symbol", "price", "qty"]])
        rows = [[symbol, info["price"], info["qty"]] for symbol, info in portfolio.items()]
        if rows:
            ws.update(range_name="A2", values=rows)
    except Exception as e:
        st.error(f"ポートフォリオの保存に失敗しました: {e}")

# --- RSI計算 ---
def calc_rsi(series, period=14):
    delta = series.diff()
    gain  = delta.clip(lower=0)
    loss  = -delta.clip(upper=0)
    avg_gain = gain.ewm(com=period - 1, min_periods=period).mean()
    avg_loss = loss.ewm(com=period - 1, min_periods=period).mean()
    rs  = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi

def rsi_label(val):
    if val >= 70:
        return f"🔴 {val:.1f}"   # 買われすぎ
    elif val <= 30:
        return f"🟢 {val:.1f}"   # 売られすぎ（反発期待）
    elif val >= 60:
        return f"🟠 {val:.1f}"   # やや過熱
    elif val <= 40:
        return f"🔵 {val:.1f}"   # やや安値圏
    else:
        return f"⚪ {val:.1f}"   # 中立

# --- ステージ判定（3本線対応・4段階）---
def check_stage(df):
    long_stage = "－"
    if len(df) >= 200 and pd.notna(df["MA200"].iloc[-1]) and pd.notna(df["MA200"].iloc[-2]):
        t_75  = df["MA75"].iloc[-1]
        y_75  = df["MA75"].iloc[-2]
        t_200 = df["MA200"].iloc[-1]
        y_200 = df["MA200"].iloc[-2]
        if y_75 <= y_200 and t_75 > t_200:
            long_stage = "🔴長期GC"
        elif y_75 >= y_200 and t_75 < t_200:
            long_stage = "🔵長期DC"
        elif t_75 > t_200:
            long_stage = "🟢長期強気"
        else:
            long_stage = "⚫長期弱気"

    if len(df) < 75:
        return "データ不足", 1.5, long_stage

    today_25     = df["MA25"].iloc[-1]
    today_75     = df["MA75"].iloc[-1]
    yesterday_25 = df["MA25"].iloc[-2]
    yesterday_75 = df["MA75"].iloc[-2]

    today_diff    = ((today_25 - today_75) / today_75) * 100
    yesterday_diff = ((yesterday_25 - yesterday_75) / yesterday_75) * 100
    today_abs     = abs(today_diff)
    yesterday_abs = abs(yesterday_diff)

    volatility        = df["MA25"].pct_change().tail(20).std() * 100
    dynamic_threshold = max(1.5, min(5.0, volatility * 15))

    if yesterday_25 <= yesterday_75 and today_25 > today_75:
        return "🔴ステージ3 (GC確定)", dynamic_threshold, long_stage
    if yesterday_25 >= yesterday_75 and today_25 < today_75:
        return "🔵ステージ3 (DC確定)", dynamic_threshold, long_stage
    if today_abs < dynamic_threshold and today_abs < yesterday_abs:
        return "🟠ステージ2 (接近・予兆)", dynamic_threshold, long_stage
    return "🟢ステージ1 (安定)", dynamic_threshold, long_stage

# --- 複合アドバイザーロジック（RSI対応）---
def get_advanced_advice(df, p_price, profit_pct, stage_text, long_stage):
    if len(df) < 5:
        return "データ不足"

    div_today     = df["Div_Pct"].iloc[-1]
    div_yesterday = df["Div_Pct"].iloc[-2]

    macd_hist_today     = df["MACD_Hist"].iloc[-1]
    macd_hist_yesterday = df["MACD_Hist"].iloc[-2]
    macd_hist_3days_ago = df["MACD_Hist"].iloc[-4]

    bb_width_today      = (df["BB_upper2"].iloc[-1] - df["BB_lower2"].iloc[-1]) / df["BB_std"].iloc[-1] * 100
    bb_width_min_10days = ((df["BB_upper2"] - df["BB_lower2"]) / df["BB_std"] * 100).tail(10).min()

    rsi_today = df["RSI"].iloc[-1]

    is_macd_turning_up   = macd_hist_today > macd_hist_yesterday and macd_hist_yesterday < macd_hist_3days_ago
    is_macd_turning_down = macd_hist_today < macd_hist_yesterday and macd_hist_yesterday > macd_hist_3days_ago
    is_bb_squeezing      = bb_width_today <= bb_width_min_10days * 1.05
    is_rsi_overbought    = rsi_today >= 70    # 買われすぎ
    is_rsi_oversold      = rsi_today <= 30    # 売られすぎ（反発期待）
    is_rsi_hot           = rsi_today >= 60    # やや過熱
    is_rsi_cool          = rsi_today <= 40    # やや安値圏

    is_above_200 = (pd.notna(df["MA200"].iloc[-1]) and df["Close"].iloc[-1] > df["MA200"].iloc[-1])
    is_long_bull = "強気" in long_stage or "長期GC" in long_stage

    # ---- 未保有 ----
    if p_price == 0:
        if is_rsi_overbought:
            return f"⛔ 【買い見送り】RSI {rsi_today:.0f}で買われすぎ圏。高値掴みのリスクあり。押し目を待つ"
        if is_bb_squeezing:
            return "💥 【嵐の前の静けさ】BB限界収縮中。上下どちらかに大きく跳ねるエネルギー蓄積。突破待ち"
        if "ステージ3 (GC確定)" in stage_text and is_long_bull and not is_rsi_hot:
            return f"✨ 【強い買い時】中期GC＋長期強気＋RSI {rsi_today:.0f}（適温）。絶好の押し目買いチャンス"
        if "ステージ3 (GC確定)" in stage_text and not is_long_bull:
            return "⚠️ 【慎重な打診買い】中期GCだが長期は弱気圏。200日線を超えるまでは少量打診にとどめる"
        if is_rsi_oversold and is_macd_turning_up:
            return f"🛒 【打診買い好機】RSI {rsi_today:.0f}の売られすぎ圏でMACDが反転の初動。底値圏の可能性"
        if div_today < 0 and is_macd_turning_up and macd_hist_today < 0:
            return "🛒 【打診買い検討】株価安値圏。MACDの売りエネルギーが底を打ち、反転の初動を検知"
        return "⏳ 特になし（トレンド見極め中）"

    # ---- 含み益 +10%以上 ----
    if profit_pct >= 10.0:
        if "ステージ3 (DC確定)" in stage_text:
            return "🚨 【全利確を推奨】中期デッドクロス確定。トレンドが完全に崩壊しました"
        if is_rsi_overbought and is_macd_turning_down:
            return f"💰 【利確強く推奨】RSI {rsi_today:.0f}の買われすぎ＋MACDピークアウト。上昇エネルギー枯渇"
        if is_macd_turning_down and div_today < div_yesterday:
            return "💰 【半分売り時】含み益十分。MACDの買いエネルギーがピークアウト。調整の兆候あり"
        if "🟢" in stage_text and df["Close"].iloc[-1] >= df["BB_upper1"].iloc[-1]:
            return "🚀 【急伸中】1σの枠を超えて上昇中。強いトレンド発生のシグナル"
        if is_above_200 and is_long_bull and not is_rsi_overbought:
            return "🏃‍♂️ 【継続保有】200日線上・長期強気相場の只中。RSIも適温。利益を伸ばす局面"
        return "🏃‍♂️ 【継続保有】中長期の上昇エネルギー維持。このまま利益を伸ばす局面"

    # ---- 小幅損益 -5%〜+10% ----
    elif -5.0 <= profit_pct < 10.0:
        if "ステージ3 (DC確定)" in stage_text:
            return "🛡️ 【撤退最優先】トントン圏でデッドクロスが確定。傷が浅いうちに現金回収を"
        if not is_above_200 and not is_long_bull:
            return "⚠️ 【200日線割れ警戒】長期弱気相場に突入の可能性。ポジション縮小を検討"
        if is_rsi_oversold and is_macd_turning_up:
            return f"🛒 【買い増し好機】RSI {rsi_today:.0f}の売られすぎ圏でMACDが反転。底打ち確認できれば買い増し"
        if div_today < 0 and is_macd_turning_up:
            return "🛒 【買い増し好機】安値圏でもみ合い中、MACDの売りエネルギーが枯渇。底打ち反転へ"
        if is_bb_squeezing:
            return "⏳ 【エネルギー蓄積】BB収縮中。上下のブレイクを見極め"
        return "⏳ 【静観】明確なサインなし。需給の拮抗状態"

    # ---- 含み損 -5%以下 ----
    else:
        if "ステージ3 (DC確定)" in stage_text:
            return "❌ 【損切り検討】デッドクロス確定。下落エネルギーがさらに強まるリスクあり"
        if not is_above_200 and not is_long_bull:
            return "🚫 【損切り優先】200日線割れ＋長期弱気相場。戻り売りに押される可能性が高い"
        if is_rsi_oversold and is_macd_turning_up:
            return f"💎 【底値圏の兆候】RSI {rsi_today:.0f}の売られすぎ＋MACD反転初動。ナンピンの検討余地あり"
        if is_macd_turning_up and df["Close"].iloc[-1] <= df["Close"].tail(5).min() * 1.02:
            return "💎 【ナンピン買い場】株価は最安値圏だが、MACDの売りエネルギーが明確に縮小"
        return "💤 【耐える局面】無理に動かず、MACDが明確に上を向いて売りエネルギーが抜けるのを待つ"

# --- 銘柄データ取得（RSI追加）---
@st.cache_data(ttl=3600)
def fetch_stock_data(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="2y")
        if df.empty:
            return None

        df["MA25"]  = df["Close"].rolling(window=25).mean()
        df["MA75"]  = df["Close"].rolling(window=75).mean()
        df["MA200"] = df["Close"].rolling(window=200).mean()
        df["Div_Pct"]     = ((df["MA25"] - df["MA75"])  / df["MA75"])  * 100
        df["LongDiv_Pct"] = ((df["MA75"] - df["MA200"]) / df["MA200"]) * 100

        df["BB_std"]    = df["Close"].rolling(window=25).mean()
        std_25          = df["Close"].rolling(window=25).std()
        df["BB_upper1"] = df["BB_std"] + std_25
        df["BB_lower1"] = df["BB_std"] - std_25
        df["BB_upper2"] = df["BB_std"] + std_25 * 2
        df["BB_lower2"] = df["BB_std"] - std_25 * 2

        ema12             = df["Close"].ewm(span=12, adjust=False).mean()
        ema26             = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"]        = ema12 - ema26
        df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["MACD_Hist"]   = df["MACD"] - df["MACD_Signal"]

        # RSI（14日）
        df["RSI"] = calc_rsi(df["Close"], period=14)

        info = ticker.info
        name = info.get("shortName", ticker_symbol)
        return {"df": df, "name": name}
    except:
        return None

# --- UI・サイドバー ---
stock_list = load_stocks()
portfolio  = load_portfolio()

st.sidebar.header("🛠️ 銘柄管理")
new_stock = st.sidebar.text_input("銘柄コード入力 (例: 7984.T):").strip().upper()
if st.sidebar.button("銘柄を追加"):
    if new_stock and new_stock not in stock_list:
        stock_list.append(new_stock)
        save_stocks(stock_list)
        st.sidebar.success(f"{new_stock} を追加しました")
        st.rerun()

stock_to_remove = st.sidebar.selectbox("削除する銘柄を選択:", [""] + stock_list)
if st.sidebar.button("選択した銘柄を削除") and stock_to_remove:
    stock_list.remove(stock_to_remove)
    if stock_to_remove in portfolio:
        del portfolio[stock_to_remove]
        save_portfolio(portfolio)
    save_stocks(stock_list)
    st.sidebar.warning(f"{stock_to_remove} を削除しました")
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("💰 保有資産の入力")
selected_p_stock = st.sidebar.selectbox("設定する銘柄:", stock_list)
if selected_p_stock:
    current_p = portfolio.get(selected_p_stock, {"price": 0.0, "qty": 0})
    p_price = st.sidebar.number_input("平均購入単価 (円):", value=float(current_p["price"]), step=1.0)
    p_qty   = st.sidebar.number_input("保有株数 (株):",     value=int(current_p["qty"]),   step=100)
    if st.sidebar.button("保有情報を保存/更新"):
        portfolio[selected_p_stock] = {"price": p_price, "qty": p_qty}
        save_portfolio(portfolio)
        st.sidebar.success(f"{selected_p_stock} の保有情報を保存しました")
        st.rerun()

# --- メイン画面 ---
if st.button("🔄 情報を最新に更新"):
    st.cache_data.clear()
    st.rerun()

summary_rows = []
all_dfs      = {}

for symbol in stock_list:
    data = fetch_stock_data(symbol)
    if data:
        df = data["df"]
        all_dfs[symbol] = df

        latest_close = df["Close"].iloc[-1]
        prev_close   = df["Close"].iloc[-2]
        change       = latest_close - prev_close
        change_pct   = (change / prev_close) * 100

        div_pct  = df["Div_Pct"].iloc[-1]     if pd.notna(df["Div_Pct"].iloc[-1])     else 0
        long_div = df["LongDiv_Pct"].iloc[-1] if pd.notna(df["LongDiv_Pct"].iloc[-1]) else None
        rsi_val  = df["RSI"].iloc[-1]         if pd.notna(df["RSI"].iloc[-1])         else 0

        stage_text, current_threshold, long_stage = check_stage(df)

        p_info    = portfolio.get(symbol, {"price": 0.0, "qty": 0})
        buy_price = p_info["price"]
        qty       = p_info["qty"]

        profit_val     = "-"
        profit_pct_val = 0.0
        profit_pct_str = "-"

        if buy_price > 0 and qty > 0:
            profit_val     = (latest_close - buy_price) * qty
            profit_pct_val = ((latest_close - buy_price) / buy_price) * 100
            profit_pct_str = f"{profit_pct_val:+.2f}%"
            profit_val     = f"{profit_val:+,.0f}円"

        advice_text = get_advanced_advice(df, buy_price, profit_pct_val, stage_text, long_stage)

        summary_rows.append({
            "コード":       symbol,
            "銘柄名":       data["name"],
            "現在値":       round(latest_close, 1),
            "前日比":       f"{change:+.1f} ({change_pct:+.2f}%)",
            "RSI":          rsi_label(rsi_val),      # ← 新規列
            "中期乖離":     f"{div_pct:+.2f}%",
            "長期乖離":     f"{long_div:+.2f}%" if long_div is not None else "-",
            "中期状態":     stage_text,
            "長期状態":     long_stage,
            "購入単価":     f"{buy_price:,.1f}円" if buy_price > 0 else "-",
            "株数":         f"{qty:,}株"          if qty > 0       else "-",
            "評価損益":     profit_val,
            "損益率":       profit_pct_str,
            "AIアドバイス": advice_text,
        })

if summary_rows:
    st.subheader("📊 監視 ＆ 保有銘柄一覧")
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    st.markdown("---")
    st.subheader("🛠️ プロフェッショナル・マルチテクニカル解析チャート")
    selected_symbol = st.selectbox("詳細チャートを表示する銘柄を選択:", stock_list)

    if selected_symbol in all_dfs:
        plot_df = all_dfs[selected_symbol].tail(120)

        fig = make_subplots(
            rows=5, cols=1, shared_xaxes=True,
            vertical_spacing=0.02,
            row_heights=[0.40, 0.15, 0.15, 0.15, 0.15]   # ← 5段構成
        )

        # 【上段：株価・移動平均3本・BB】
        fig.add_trace(go.Candlestick(
            x=plot_df.index, open=plot_df['Open'], high=plot_df['High'],
            low=plot_df['Low'], close=plot_df['Close'], name='株価'), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['MA25'],  name='25日線',
            line=dict(color='#FFA500', width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['MA75'],  name='75日線',
            line=dict(color='#1F77B4', width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['MA200'], name='200日線',
            line=dict(color='#E74C3C', width=2.0, dash='dot')), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['BB_upper2'], name='BB +2σ',
            line=dict(color='rgba(128,128,128,0.3)', width=1, dash='dash')), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['BB_lower2'], name='BB -2σ',
            line=dict(color='rgba(128,128,128,0.3)', width=1, dash='dash'),
            fill='tonexty', fillcolor='rgba(128,128,128,0.02)'), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['BB_upper1'], name='BB +1σ',
            line=dict(color='rgba(100,149,237,0.25)', width=1, dash='dot')), row=1, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['BB_lower1'], name='BB -1σ',
            line=dict(color='rgba(100,149,237,0.25)', width=1, dash='dot'),
            fill='tonexty', fillcolor='rgba(100,149,237,0.04)'), row=1, col=1)

        # 【2段目：中期乖離率】
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['Div_Pct'], name='中期乖離率(%)',
            line=dict(color='#9467BD', width=2),
            fill='tozeroy', fillcolor='rgba(148,103,189,0.05)'), row=2, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=[0]*len(plot_df),
            line=dict(color='gray', width=1, dash='dash'), showlegend=False), row=2, col=1)

        # 【3段目：長期乖離率】
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['LongDiv_Pct'], name='長期乖離率(%)',
            line=dict(color='#E74C3C', width=2),
            fill='tozeroy', fillcolor='rgba(231,76,60,0.05)'), row=3, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=[0]*len(plot_df),
            line=dict(color='gray', width=1, dash='dash'), showlegend=False), row=3, col=1)

        # 【4段目：MACD】
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['MACD'], name='MACD',
            line=dict(color='#00CC96', width=1.5)), row=4, col=1)
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['MACD_Signal'], name='シグナル',
            line=dict(color='#EF553B', width=1.5)), row=4, col=1)
        fig.add_trace(go.Bar(x=plot_df.index, y=plot_df['MACD_Hist'], name='ヒストグラム',
            marker_color='rgba(100,149,237,0.4)'), row=4, col=1)

        # 【5段目：RSI】← 新規
        fig.add_trace(go.Scatter(x=plot_df.index, y=plot_df['RSI'], name='RSI(14)',
            line=dict(color='#FF7F0E', width=2)), row=5, col=1)
        # 70ライン（買われすぎ）
        fig.add_hline(y=70, line=dict(color='red',  width=1, dash='dash'), row=5, col=1)
        # 30ライン（売られすぎ）
        fig.add_hline(y=30, line=dict(color='blue', width=1, dash='dash'), row=5, col=1)
        # 50ライン（中立）
        fig.add_hline(y=50, line=dict(color='gray', width=1, dash='dot'),  row=5, col=1)
        # 70〜30の帯をうっすら色付け
        fig.add_hrect(y0=70, y1=100, fillcolor='rgba(255,0,0,0.03)',  line_width=0, row=5, col=1)
        fig.add_hrect(y0=0,  y1=30,  fillcolor='rgba(0,0,255,0.03)',  line_width=0, row=5, col=1)

        fig.update_layout(
            title=f"{selected_symbol} 総合マルチ解析（25日・75日・200日・RSI）",
            xaxis_rangeslider_visible=False,
            xaxis2_rangeslider_visible=False,
            xaxis3_rangeslider_visible=False,
            xaxis4_rangeslider_visible=False,
            xaxis5_rangeslider_visible=False,
            template="plotly_white",
            height=1000
        )
        fig.update_yaxes(title_text="株価/BB",    row=1, col=1)
        fig.update_yaxes(title_text="中期乖離率", row=2, col=1)
        fig.update_yaxes(title_text="長期乖離率", row=3, col=1)
        fig.update_yaxes(title_text="MACD",       row=4, col=1)
        fig.update_yaxes(title_text="RSI",        row=5, col=1, range=[0, 100])

        st.plotly_chart(fig, use_container_width=True)
