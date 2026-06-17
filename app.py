import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import json
import os

# --- 定数と設定 ---
CONFIG_FILE = "monitored_stocks.json"
PORTFOLIO_FILE = "my_portfolio.json"
DEFAULT_STOCKS = ["7203.T", "9984.T", "6758.T", "7974.T", "6501.T", "5803.T", "7984.T", "7453.T"]

# ページ設定
st.set_page_config(page_title="Professional Stock Monitor", layout="wide")
st.title("📈 株式クロス監視 ＆ プロ仕様複合アドバイザーダッシュボード")

# --- データ読み込み・保存関数 ---
def load_stocks():
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r") as f:
            return json.load(f)
    return DEFAULT_STOCKS

def save_stocks(stock_list):
    with open(CONFIG_FILE, "w") as f:
        json.dump(stock_list, f)

def load_portfolio():
    if os.path.exists(PORTFOLIO_FILE):
        with open(PORTFOLIO_FILE, "r") as f:
            return json.load(f)
    return {}

def save_portfolio(portfolio):
    with open(PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f)

# --- ステージ判定（3本線対応・4段階）---
def check_stage(df):
    # 200日線クロス判定（長期トレンド）
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

    # 25日・75日線クロス判定（中期トレンド）
    if len(df) < 75:
        return "データ不足", 1.5, long_stage

    today_25    = df["MA25"].iloc[-1]
    today_75    = df["MA75"].iloc[-1]
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

# --- 複合アドバイザーロジック（200日線対応）---
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

    is_macd_turning_up   = macd_hist_today > macd_hist_yesterday and macd_hist_yesterday < macd_hist_3days_ago
    is_macd_turning_down = macd_hist_today < macd_hist_yesterday and macd_hist_yesterday > macd_hist_3days_ago
    is_bb_squeezing      = bb_width_today <= bb_width_min_10days * 1.05

    # 200日線との位置関係フラグ
    is_above_200 = (pd.notna(df["MA200"].iloc[-1]) and
                    df["Close"].iloc[-1] > df["MA200"].iloc[-1])
    is_long_bull = "強気" in long_stage or "長期GC" in long_stage

    # ---- 未保有 ----
    if p_price == 0:
        if is_bb_squeezing:
            return "💥 【嵐の前の静けさ】BB限界収縮中。上下どちらかに大きく跳ねるエネルギー蓄積。突破待ち"
        if "ステージ3 (GC確定)" in stage_text and is_long_bull:
            return "✨ 【強い買い時】中期GC＋長期強気相場が一致。トレンド大転換の押し目買いチャンス"
        if "ステージ3 (GC確定)" in stage_text and not is_long_bull:
            return "⚠️ 【慎重な打診買い】中期GCだが長期は弱気圏。200日線を超えるまでは少量打診にとどめる"
        if div_today < 0 and is_macd_turning_up and macd_hist_today < 0:
            return "🛒 【打診買い検討】株価安値圏。MACDの売りエネルギーが底を打ち、反転の初動を検知"
        return "⏳ 特になし（トレンド見極め中）"

    # ---- 含み益 +10%以上 ----
    if profit_pct >= 10.0:
        if "ステージ3 (DC確定)" in stage_text:
            return "🚨 【全利確を推奨】中期デッドクロス確定。トレンドが完全に崩壊しました"
        if is_macd_turning_down and div_today < div_yesterday:
            return "💰 【半分売り時】含み益十分。MACDの買いエネルギーがピークアウト。調整の兆候あり"
        if "🟢" in stage_text and df["Close"].iloc[-1] >= df["BB_upper1"].iloc[-1]:
            return "🚀 【急伸中】1σの枠を超えて上昇中。強いトレンド発生のシグナル"
        if is_above_200 and is_long_bull:
            return "🏃‍♂️ 【継続保有】200日線上・長期強気相場の只中。上昇エネルギー維持。利益を伸ばす局面"
        return "🏃‍♂️ 【継続保有】中長期の上昇エネルギー維持。このまま利益を伸ばす局面"

    # ---- 小幅損益 -5%〜+10% ----
    elif -5.0 <= profit_pct < 10.0:
        if "ステージ3 (DC確定)" in stage_text:
            return "🛡️ 【撤退最優先】トントン圏でデッドクロスが確定。傷が浅いうちに現金回収を"
        if not is_above_200 and not is_long_bull:
            return "⚠️ 【200日線割れ警戒】長期弱気相場に突入の可能性。ポジション縮小を検討"
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
        if is_macd_turning_up and df["Close"].iloc[-1] <= df["Close"].tail(5).min() * 1.02:
            return "💎 【ナンピン買い場】株価は最安値圏だが、MACDの売りエネルギーが明確に縮小（ダイバージェンスの芽）"
        return "💤 【耐える局面】無理に動かず、MACDが明確に上を向いて売りエネルギーが抜けるのを待つ"

# --- 銘柄情報の取得とプロ指標の計算（データ期間：2年）---
@st.cache_data(ttl=3600)
def fetch_stock_data(ticker_symbol):
    try:
        ticker = yf.Ticker(ticker_symbol)
        df = ticker.history(period="2y")   # ← 6mo → 2y に変更
        if df.empty:
            return None

        # 移動平均線（25日・75日・200日）
        df["MA25"]  = df["Close"].rolling(window=25).mean()
        df["MA75"]  = df["Close"].rolling(window=75).mean()
        df["MA200"] = df["Close"].rolling(window=200).mean()   # ← 新規追加

        # 線間乖離率（25日 vs 75日：中期トレンドの強さ）
        df["Div_Pct"] = ((df["MA25"] - df["MA75"]) / df["MA75"]) * 100

        # 長期乖離率（75日 vs 200日：長期トレンドの強さ）← 新規追加
        df["LongDiv_Pct"] = ((df["MA75"] - df["MA200"]) / df["MA200"]) * 100

        # ボリンジャーバンド（25日基準、±1σ・±2σ）
        df["BB_std"]    = df["Close"].rolling(window=25).mean()
        std_25          = df["Close"].rolling(window=25).std()
        df["BB_upper1"] = df["BB_std"] + (std_25 * 1)
        df["BB_lower1"] = df["BB_std"] - (std_25 * 1)
        df["BB_upper2"] = df["BB_std"] + (std_25 * 2)
        df["BB_lower2"] = df["BB_std"] - (std_25 * 2)

        # MACD
        ema12            = df["Close"].ewm(span=12, adjust=False).mean()
        ema26            = df["Close"].ewm(span=26, adjust=False).mean()
        df["MACD"]       = ema12 - ema26
        df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
        df["MACD_Hist"]  = df["MACD"] - df["MACD_Signal"]

        info = ticker.info
        name = info.get("shortName", ticker_symbol)

        return {"df": df, "name": name}
    except:
        return None

# --- UI・サイドバー構成 ---
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

# --- メイン画面：データ処理 ---
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

        div_pct  = df["Div_Pct"].iloc[-1]  if pd.notna(df["Div_Pct"].iloc[-1])  else 0
        long_div = df["LongDiv_Pct"].iloc[-1] if pd.notna(df["LongDiv_Pct"].iloc[-1]) else None

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
            "コード":     symbol,
            "銘柄名":     data["name"],
            "現在値":     round(latest_close, 1),
            "前日比":     f"{change:+.1f} ({change_pct:+.2f}%)",
            "中期乖離":   f"{div_pct:+.2f}%",
            "長期乖離":   f"{long_div:+.2f}%" if long_div is not None else "-",  # ← 新規列
            "中期状態":   stage_text,
            "長期状態":   long_stage,                                              # ← 新規列
            "購入単価":   f"{buy_price:,.1f}円" if buy_price > 0 else "-",
            "株数":       f"{qty:,}株"          if qty > 0       else "-",
            "評価損益":   profit_val,
            "損益率":     profit_pct_str,
            "AIアドバイス": advice_text,
        })

if summary_rows:
    st.subheader("📊 監視 ＆ 保有銘柄一覧")
    st.dataframe(pd.DataFrame(summary_rows), use_container_width=True, hide_index=True)

    # --- 詳細チャート表示 ---
    st.markdown("---")
    st.subheader("🛠️ プロフェッショナル・マルチテクニカル解析チャート")
    selected_symbol = st.selectbox("詳細チャートを表示する銘柄を選択:", stock_list)

    if selected_symbol in all_dfs:
        plot_df = all_dfs[selected_symbol].tail(120)   # ← 60日 → 120日に拡張

        fig = make_subplots(
            rows=4, cols=1, shared_xaxes=True,
            vertical_spacing=0.03,
            row_heights=[0.45, 0.20, 0.20, 0.15]     # ← 4段構成（長期乖離率を追加）
        )

        # 【上段：株価 ＆ 移動平均線3本 ＆ ボリンジャーバンド】
        fig.add_trace(go.Candlestick(
            x=plot_df.index, open=plot_df['Open'], high=plot_df['High'],
            low=plot_df['Low'], close=plot_df['Close'], name='株価'), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['MA25'],  name='25日線',
            line=dict(color='#FFA500', width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['MA75'],  name='75日線',
            line=dict(color='#1F77B4', width=1.5)), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['MA200'], name='200日線',
            line=dict(color='#E74C3C', width=2.0, dash='dot')), row=1, col=1)  # ← 新規

        # BB ±2σ
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['BB_upper2'], name='BB +2σ',
            line=dict(color='rgba(128,128,128,0.3)', width=1, dash='dash')), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['BB_lower2'], name='BB -2σ',
            line=dict(color='rgba(128,128,128,0.3)', width=1, dash='dash'),
            fill='tonexty', fillcolor='rgba(128,128,128,0.02)'), row=1, col=1)

        # BB ±1σ
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['BB_upper1'], name='BB +1σ',
            line=dict(color='rgba(100,149,237,0.25)', width=1, dash='dot')), row=1, col=1)
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['BB_lower1'], name='BB -1σ',
            line=dict(color='rgba(100,149,237,0.25)', width=1, dash='dot'),
            fill='tonexty', fillcolor='rgba(100,149,237,0.04)'), row=1, col=1)

        # 【2段目：中期線間乖離率（25日 vs 75日）】
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['Div_Pct'], name='中期乖離率(%)',
            line=dict(color='#9467BD', width=2),
            fill='tozeroy', fillcolor='rgba(148,103,189,0.05)'), row=2, col=1)
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=[0]*len(plot_df),
            line=dict(color='gray', width=1, dash='dash'), showlegend=False), row=2, col=1)

        # 【3段目：長期線間乖離率（75日 vs 200日）】← 新規
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['LongDiv_Pct'], name='長期乖離率(%)',
            line=dict(color='#E74C3C', width=2),
            fill='tozeroy', fillcolor='rgba(231,76,60,0.05)'), row=3, col=1)
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=[0]*len(plot_df),
            line=dict(color='gray', width=1, dash='dash'), showlegend=False), row=3, col=1)

        # 【4段目：MACD】
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['MACD'], name='MACD',
            line=dict(color='#00CC96', width=1.5)), row=4, col=1)
        fig.add_trace(go.Scatter(
            x=plot_df.index, y=plot_df['MACD_Signal'], name='シグナル',
            line=dict(color='#EF553B', width=1.5)), row=4, col=1)
        fig.add_trace(go.Bar(
            x=plot_df.index, y=plot_df['MACD_Hist'], name='ヒストグラム',
            marker_color='rgba(100,149,237,0.4)'), row=4, col=1)

        fig.update_layout(
            title=f"{selected_symbol} 総合マルチ解析（25日・75日・200日）",
            xaxis_rangeslider_visible=False,
            xaxis2_rangeslider_visible=False,
            xaxis3_rangeslider_visible=False,
            xaxis4_rangeslider_visible=False,
            template="plotly_white",
            height=900
        )
        fig.update_yaxes(title_text="株価/BB",    row=1, col=1)
        fig.update_yaxes(title_text="中期乖離率", row=2, col=1)
        fig.update_yaxes(title_text="長期乖離率", row=3, col=1)
        fig.update_yaxes(title_text="MACD",       row=4, col=1)

        st.plotly_chart(fig, use_container_width=True)
