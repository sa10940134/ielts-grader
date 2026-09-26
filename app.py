from datetime import date, datetime
import json
import sqlite3
import time
from google import genai
from google.genai import types
from google.genai.errors import APIError
import matplotlib.pyplot as plt
import numpy as np
import streamlit as st

st.set_page_config(page_title="雅思寫作雙任務批改系統", layout="wide")
st.title("✍️ 雅思寫作 Task 1 & Task 2 智能出題與全套批改系統")

MODEL_NAME = "gemini-2.0-flash"
# ===========================
# 0. 資料庫設定
# ===========================
DB_FILE = "usage_tracker.db"


def get_connection():
  return sqlite3.connect(DB_FILE, check_same_thread=False)


def init_db():
  with get_connection() as conn:
    c = conn.cursor()
    c.execute("""
            CREATE TABLE IF NOT EXISTS students (
                student_id TEXT PRIMARY KEY,
                name TEXT,
                created_at TEXT
            )
        """)
    c.execute("""
            CREATE TABLE IF NOT EXISTS usage (
                student_id TEXT,
                date TEXT,
                count INTEGER,
                PRIMARY KEY (student_id, date)
            )
        """)
    conn.commit()


def is_student_registered(student_id: str) -> bool:
  with get_connection() as conn:
    c = conn.cursor()
    c.execute("SELECT 1 FROM students WHERE student_id = ?", (student_id,))
    return c.fetchone() is not None


def register_student(student_id: str, name: str) -> bool:
  try:
    with get_connection() as conn:
      c = conn.cursor()
      c.execute(
          "INSERT INTO students (student_id, name, created_at) VALUES (?, ?,"
          " ?)",
          (student_id, name, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
      )
      conn.commit()
      return True
  except sqlite3.IntegrityError:
    return False


def get_today_usage(student_id: str) -> int:
  today_str = date.today().isoformat()
  with get_connection() as conn:
    c = conn.cursor()
    c.execute(
        "SELECT count FROM usage WHERE student_id = ? AND date = ?",
        (student_id, today_str),
    )
    row = c.fetchone()
    return row[0] if row else 0


def increment_usage(student_id: str):
  today_str = date.today().isoformat()
  with get_connection() as conn:
    c = conn.cursor()
    c.execute(
        """
            INSERT INTO usage (student_id, date, count)
            VALUES (?, ?, 1)
            ON CONFLICT(student_id, date)
            DO UPDATE SET count = count + 1
        """,
        (student_id, today_str),
    )
    conn.commit()


init_db()


def generate_with_retry(client, **kwargs):
  """遇到 503 或伺服器過載時，自動重試最多 3 次"""
  max_retries = 3
  delay = 2.0
  for attempt in range(max_retries):
    try:
      return client.models.generate_content(**kwargs)
    except APIError as e:
      if getattr(e, "code", None) in [503, 429] and attempt < max_retries - 1:
        time.sleep(delay)
        delay *= 2
        continue
      raise e
    except Exception as e:
      if "503" in str(e) and attempt < max_retries - 1:
        time.sleep(delay)
        delay *= 2
        continue
      raise e


# ===========================
# 1. 驗證與側邊欄
# ===========================
DAILY_LIMIT = 3

api_key = st.secrets.get("GEMINI_API_KEY", "")
if not api_key:
  api_key = st.sidebar.text_input("Gemini API Key：", type="password")

if not api_key:
  st.warning("⚠️ 系統尚未設定 API Key，請在 secrets.toml 或左側輸入！")
  st.stop()

client = genai.Client(api_key=api_key)

st.sidebar.header("🎓 學員中心")
auth_mode = st.sidebar.radio("請選擇操作：", ["登入系統", "註冊新學號"])

active_student_id = None

if auth_mode == "註冊新學號":
  new_id = st.sidebar.text_input("設定新學號（如：ielts001）：").strip().lower()
  new_name = st.sidebar.text_input("學員姓名 / 暱稱：").strip()

  if st.sidebar.button("確認建立學號"):
    if not new_id or not new_name:
      st.sidebar.error("學號與姓名皆不可留空！")
    elif is_student_registered(new_id):
      st.sidebar.error(f"學號 `{new_id}` 已被使用，請更換！")
    else:
      if register_student(new_id, new_name):
        st.sidebar.success(
            f"🎉 建立成功！學號：`{new_id}`。請切換至「登入系統」。"
        )
      else:
        st.sidebar.error("建立失敗，請稍後重試。")
  st.stop()
else:
  login_id = (
      st.sidebar.text_input("請輸入學員編號：", placeholder="例如：ielts001")
      .strip()
      .lower()
  )
  if not login_id:
    st.info("👈 請在左側輸入學員編號登入；新同學請先註冊。")
    st.stop()
  if not is_student_registered(login_id):
    st.sidebar.error("❌ 找不到此學號，請確認輸入或前往註冊！")
    st.stop()
  active_student_id = login_id

used_count = get_today_usage(active_student_id)
remaining_count = max(0, DAILY_LIMIT - used_count)

st.sidebar.markdown("---")
st.sidebar.markdown(f"**目前登入：** `{active_student_id}`")
st.sidebar.metric("今日剩餘額度", f"{remaining_count} / {DAILY_LIMIT}")

if remaining_count == 0:
  st.sidebar.error("❌ 今日批改額度已用完，明日重置！")

# ===========================
# 2. 輔助繪圖函式 (Task 1 視覺化)
# ===========================


def plot_task1_chart(chart_data):
  """根據模型回傳的資料繪製 Matplotlib 圖表"""
  fig, ax = plt.subplots(figsize=(8, 4.5))
  chart_type = chart_data.get("chart_type", "bar")
  categories = chart_data.get("categories", [])
  series_list = chart_data.get("series", [])

  if chart_type == "line":
    for s in series_list:
      ax.plot(
          categories,
          s.get("values", []),
          marker="o",
          linewidth=2.5,
          label=s.get("name", ""),
      )
  else:  # 預設為 bar chart
    x = np.arange(len(categories))
    num_series = max(1, len(series_list))
    width = 0.8 / num_series
    for i, s in enumerate(series_list):
      offset = (i - num_series / 2 + 0.5) * width
      ax.bar(
          x + offset,
          s.get("values", []),
          width,
          label=s.get("name", ""),
          alpha=0.9,
      )
    ax.set_xticks(x)
    ax.set_xticklabels(categories)

  ax.set_title(chart_data.get("title", "IELTS Academic Task 1 Chart"), pad=15)
  ax.set_xlabel(chart_data.get("x_label", ""))
  ax.set_ylabel(chart_data.get("y_label", ""))
  ax.grid(True, linestyle="--", alpha=0.5, axis="y")
  ax.legend(loc="upper right")
  fig.tight_layout()
  return fig


# 預設題目狀態
if "task1_data" not in st.session_state:
  st.session_state.task1_data = {
      "prompt": (
          "The chart below shows the percentage of mobile phone owners using"
          " various mobile phone features in 2006, 2008, and 2010. Summarise"
          " the information by selecting and reporting the main features, and"
          " make comparisons where relevant."
      ),
      "chart_type": "bar",
      "title": (
          "Usage of Mobile Phone Features (2006-2010) in Percentage (%)"
      ),
      "x_label": "Features",
      "y_label": "Percentage (%)",
      "categories": ["Calls", "Photos", "Texts", "Games", "Internet"],
      "series": [
          {"name": "2006", "values": [100, 66, 73, 17, 0]},
          {"name": "2008", "values": [100, 71, 75, 42, 41]},
          {"name": "2010", "values": [99, 76, 79, 41, 73]},
      ],
  }

if "task2_question" not in st.session_state:
  st.session_state.task2_question = (
      "Some people believe that university education should be free for"
      " everyone. To what extent do you agree or disagree?"
  )

# ===========================
# 3. 出題區
# ===========================
st.subheader("📋 考題設定")
c_btn1, c_btn2 = st.columns(2)

with c_btn1:
  if st.button("🎲 隨機產生 Task 1 圖表考題 (含真實圖表)"):
    with st.spinner("AI 正在繪製與生成 Task 1 圖表題目..."):
      t1_gen_prompt = """
            請生成一道雅思學術組 (Academic) Task 1 題目，並提供可用於繪圖的數據結構。
            必須輸出純 JSON，格式如下：
            {
              "prompt": "題目說明文字 (Summarise the information by selecting and reporting the main features...)",
              "chart_type": "bar 或 line",
              "title": "圖表標題",
              "x_label": "X 軸名稱",
              "y_label": "Y 軸單位 (如 % 或 Millions)",
              "categories": ["項目1", "項目2", "項目3", "項目4"],
              "series": [
                {"name": "分組A", "values": [10, 25, 30, 45]},
                {"name": "分組B", "values": [15, 20, 35, 40]}
              ]
            }
            數值要合理、具備對比性與趨勢。
            """
      try:
        res = generate_with_retry(
            client,
            model=MODEL_NAME,
            contents=t1_gen_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.7
            ),
        )
        st.session_state.task1_data = json.loads(res.text)
        st.rerun()
      except Exception as e:
        st.error(f"Task 1 出題失敗：{e}")

with c_btn2:
  if st.button("🎲 隨機產生 Task 2 申論大作文題目"):
    with st.spinner("產生 Task 2 題目中..."):
      try:
        res = generate_with_retry(
            client,
            model=MODEL_NAME,
            contents=(
                "請隨機產生一道雅思寫作 Task 2 題目，只需輸出英文題目本身。"
            ),
        )
        st.session_state.task2_question = res.text.strip()
        st.rerun()
      except Exception as e:
        st.error(f"Task 2 出題失敗：{e}")

# 顯示兩道題目與圖表
q_col1, q_col2 = st.columns(2)
with q_col1:
  st.markdown("#### 📊 Task 1 題目（圖表分析）")
  st.info(st.session_state.task1_data.get("prompt", ""))
  try:
    fig = plot_task1_chart(st.session_state.task1_data)
    st.pyplot(fig)
  except Exception as e:
    st.warning(f"圖表繪製發生微小問題：{e}")

with q_col2:
  st.markdown("#### 📝 Task 2 題目（大作文）")
  st.info(st.session_state.task2_question)

# ===========================
# 4. 作文輸入區
# ===========================
st.subheader("✍️ 學生作文輸入區")
in_col1, in_col2 = st.columns(2)

with in_col1:
  user_task1 = st.text_area(
      "請在此輸入 Task 1 作文（建議 150 字以上）：", height=280
  )
  w_count1 = len(user_task1.split()) if user_task1 else 0
  st.caption(f"Task 1 字數：{w_count1} 字 (建議 ≥ 150)")

with in_col2:
  user_task2 = st.text_area(
      "請在此輸入 Task 2 作文（建議 250 字以上）：", height=280
  )
  w_count2 = len(user_task2.split()) if user_task2 else 0
  st.caption(f"Task 2 字數：{w_count2} 字 (建議 ≥ 250)")

# ===========================
# 5. 全套雙任務批改
# ===========================
submit_disabled = remaining_count <= 0

if st.button("🚀 開始全套批改（Task 1 + Task 2）", disabled=submit_disabled):
  if remaining_count <= 0:
    st.error("您今日的批改額度已用完！")
  elif w_count1 < 40 or w_count2 < 50:
    st.error(
        "兩篇作文皆需填寫！Task 1 請至少輸入 40 字，Task 2 請至少輸入 50"
        " 字以上。"
    )
  else:
    with st.spinner(
        "雅思考官正在依官方四項標準批改雙任務，並加權計算總成績中..."
    ):
      grading_prompt = f"""
            你是一名資深雅思官方考官。請依據官方四項標準同時嚴格批改學生的 Task 1 與 Task 2 作文。

            【考卷資料】
            Task 1 題目與數據資訊：
            {json.dumps(st.session_state.task1_data, ensure_ascii=False)}
            Task 1 學生作文：
            {user_task1}

            Task 2 題目：
            {st.session_state.task2_question}
            Task 2 學生作文：
            {user_task2}

            請輸出符合以下架構的純 JSON 結構：
            {{
              "writing_overall_band": 6.5,
              "task1": {{
                "overall_band": 6.5,
                "criteria": {{
                  "ta_score": 6.5,
                  "ta_feedback": "Task Achievement 繁中點評",
                  "cc_score": 6.0,
                  "cc_feedback": "Coherence and Cohesion 繁中點評",
                  "lr_score": 6.5,
                  "lr_feedback": "Lexical Resource 繁中點評",
                  "gra_score": 6.0,
                  "gra_feedback": "Grammatical Range and Accuracy 繁中點評"
                }},
                "corrections": [
                  {{"original": "錯誤句", "improved": "改進句", "reason": "修改原因"}}
                ],
                "model_revision": "Task 1 的 8.0+ 示範改寫"
              }},
              "task2": {{
                "overall_band": 6.5,
                "criteria": {{
                  "tr_score": 6.5,
                  "tr_feedback": "Task Response 繁中點評",
                  "cc_score": 6.0,
                  "cc_feedback": "Coherence and Cohesion 繁中點評",
                  "lr_score": 6.5,
                  "lr_feedback": "Lexical Resource 繁中點評",
                  "gra_score": 6.0,
                  "gra_feedback": "Grammatical Range and Accuracy 繁中點評"
                }},
                "corrections": [
                  {{"original": "錯誤句", "improved": "改進句", "reason": "修改原因"}}
                ],
                "model_revision": "Task 2 的 8.0+ 示範改寫"
              }}
            }}

            說明：
            1. Task 1 評分項目為 TA, CC, LR, GRA；Task 2 評分項目為 TR, CC, LR, GRA。
            2. writing_overall_band 計算公式為 (Task 1 + Task 2 * 2) / 3 並四捨五入至半步進 (如 6.0, 6.5, 7.0)。
            """

      try:
        response = generate_with_retry(
            client,
            model=MODEL_NAME,
            contents=grading_prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", temperature=0.2
            ),
        )

        result = json.loads(response.text)
        increment_usage(active_student_id)

        st.success("🎉 全套批改完成！")
        st.header(
            f"🏅 雅思寫作預估總成績：Band"
            f" {result.get('writing_overall_band', 'N/A')}"
        )
        st.caption("計算方式：(Task 1 × 1/3) + (Task 2 × 2/3)")

        tab1, tab2 = st.tabs(["📊 Task 1 批改診斷", "📝 Task 2 批改診斷"])

        # --- Task 1 診斷 ---
        with tab1:
          t1 = result.get("task1", {})
          st.subheader(f"Task 1 分數：Band {t1.get('overall_band', 'N/A')}")
          c1, c2, c3, c4 = st.columns(4)
          crit1 = t1.get("criteria", {})
          c1.metric("Task Achievement", crit1.get("ta_score", "-"))
          c2.metric("Coherence & Cohesion", crit1.get("cc_score", "-"))
          c3.metric("Lexical Resource", crit1.get("lr_score", "-"))
          c4.metric("Grammar", crit1.get("gra_score", "-"))

          st.markdown("#### 📌 四項標準詳細診斷")
          st.write(f"- **TA 數據抓取與呈現：** {crit1.get('ta_feedback', '')}")
          st.write(f"- **CC 邏輯組織與分段：** {crit1.get('cc_feedback', '')}")
          st.write(f"- **LR 數據與趨勢字彙：** {crit1.get('lr_feedback', '')}")
          st.write(f"- **GRA 句型結構與準確度：** {crit1.get('gra_feedback', '')}")

          st.markdown("#### ✏️ 重點句子潤飾")
          for item in t1.get("corrections", []):
            st.markdown(f"- **原文：** `{item.get('original', '')}`")
            st.markdown(f"  **建議：** `{item.get('improved', '')}`")
            st.caption(f"  *原因：{item.get('reason', '')}*")

          st.markdown("#### 🌟 考官高分範文 (Band 8.0+)")
          st.write(t1.get("model_revision", ""))

        # --- Task 2 診斷 ---
        with tab2:
          t2 = result.get("task2", {})
          st.subheader(f"Task 2 分數：Band {t2.get('overall_band', 'N/A')}")
          c1, c2, c3, c4 = st.columns(4)
          crit2 = t2.get("criteria", {})
          c1.metric("Task Response", crit2.get("tr_score", "-"))
          c2.metric("Coherence & Cohesion", crit2.get("cc_score", "-"))
          c3.metric("Lexical Resource", crit2.get("lr_score", "-"))
          c4.metric("Grammar", crit2.get("gra_score", "-"))

          st.markdown("#### 📌 四項標準詳細診斷")
          st.write(f"- **TR 審題與論點完整性：** {crit2.get('tr_feedback', '')}")
          st.write(f"- **CC 連貫與文章銜接：** {crit2.get('cc_feedback', '')}")
          st.write(f"- **LR 學術用字與替換詞：** {crit2.get('lr_feedback', '')}")
          st.write(f"- **GRA 複合句與文法精確度：** {crit2.get('gra_feedback', '')}")

          st.markdown("#### ✏️ 重點句子潤飾")
          for item in t2.get("corrections", []):
            st.markdown(f"- **原文：** `{item.get('original', '')}`")
            st.markdown(f"  **建議：** `{item.get('improved', '')}`")
            st.caption(f"  *原因：{item.get('reason', '')}*")

          st.markdown("#### 🌟 考官高分範文 (Band 8.0+)")
          st.write(t2.get("model_revision", ""))

      except json.JSONDecodeError:
        st.error("模型輸出格式非合法 JSON，請重試一次！")
      except Exception as e:
        st.error(f"批改過程發生錯誤：{str(e)}")
