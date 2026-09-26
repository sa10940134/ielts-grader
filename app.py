from datetime import date, datetime
import json
import sqlite3
import time
from google import genai
from google.genai import types
from google.genai.errors import APIError
import streamlit as st
import streamlit as st
from google import genai
from google.genai import types
import json
import sqlite3
from datetime import date, datetime

st.set_page_config(page_title="雅思寫作批改系統", layout="wide")
st.title("✍️ 雅思寫作 Task 2 智能出題與批改系統")

# ===========================
# 0. 資料庫設定
# ===========================
DB_FILE = "usage_tracker.db"

def get_connection():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def init_db():
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS students (
                student_id TEXT PRIMARY KEY,
                name TEXT,
                created_at TEXT
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS usage (
                student_id TEXT,
                date TEXT,
                count INTEGER,
                PRIMARY KEY (student_id, date)
            )
        ''')
        conn.commit()

def is_student_registered(student_id: str) -> bool:
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT 1 FROM students WHERE student_id = ?', (student_id,))
        return c.fetchone() is not None

def register_student(student_id: str, name: str) -> bool:
    try:
        with get_connection() as conn:
            c = conn.cursor()
            c.execute(
                'INSERT INTO students (student_id, name, created_at) VALUES (?, ?, ?)',
                (student_id, name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            )
            conn.commit()
            return True
    except sqlite3.IntegrityError:
        return False

def get_today_usage(student_id: str) -> int:
    today_str = date.today().isoformat()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('SELECT count FROM usage WHERE student_id = ? AND date = ?', (student_id, today_str))
        row = c.fetchone()
        return row[0] if row else 0

def increment_usage(student_id: str):
    today_str = date.today().isoformat()
    with get_connection() as conn:
        c = conn.cursor()
        c.execute('''
            INSERT INTO usage (student_id, date, count)
            VALUES (?, ?, 1)
            ON CONFLICT(student_id, date)
            DO UPDATE SET count = count + 1
        ''', (student_id, today_str))
        conn.commit()

init_db()
def generate_with_retry(client, **kwargs):
  """遇到 503 或伺服器過載時，自動重試最多 3 次"""
  max_retries = 3
  delay = 2.0  # 初始等待 2 秒

  for attempt in range(max_retries):
    try:
      return client.models.generate_content(**kwargs)
    except APIError as e:
      # 捕捉 503 (UNAVAILABLE) 或 429 (RESOURCE_EXHAUSTED)
      if e.code in [503, 429] and attempt < max_retries - 1:
        time.sleep(delay)
        delay *= 2  # 指數退避：2s -> 4s
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
    api_key = st.sidebar.text_input("Gemini API Key（管理員設定）：", type="password")

if not api_key:
    st.warning("⚠️ 系統尚未設定 API Key，請聯繫管理員！")
    st.stop()

# 初始化 GenAI Client
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
                st.sidebar.success(f"🎉 建立成功！學號：`{new_id}`。請切換至「登入系統」。")
            else:
                st.sidebar.error("建立失敗，請稍後重試。")
    st.stop()
else:
    login_id = st.sidebar.text_input("請輸入學員編號：", placeholder="例如：ielts001").strip().lower()
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
# 2. 隨機出題
# ===========================
if "question" not in st.session_state:
    st.session_state.question = "Some people believe that university education should be free for everyone. To what extent do you agree or disagree?"

if st.button("🎲 隨機產生雅思 Task 2 題目"):
    with st.spinner("正在產生題目..."):
        try:
            res = generate_with_retry(
    client,
    model="gemini-3.8-flash",
    contents="請隨機產生一道雅思寫作 Task 2 題目，只需輸出英文題目本身，不需要任何引言或問候語。"
)
            st.session_state.question = res.text.strip()
            st.rerun()
        except Exception as e:
            st.error(f"出題失敗：{e}")

st.info(f"**當前題目：**\n\n{st.session_state.question}")

# ===========================
# 3. 學生作文輸入
# ===========================
user_essay = st.text_area("請在此貼上作文（建議 250 字以上）：", height=250)
word_count = len(user_essay.split()) if user_essay else 0
st.caption(f"目前字數：{word_count} 字")

# ===========================
# 4. 批改與評分
# ===========================
submit_disabled = (remaining_count <= 0)

if st.button("🚀 開始批改與評分", disabled=submit_disabled):
    if remaining_count <= 0:
        st.error("您今日的批改額度已用完！")
    elif word_count < 50:
        st.error("文章字數過少，請至少輸入 50 字以上！")
    else:
        with st.spinner("雅思考官正在評分中，請稍候..."):
            grading_prompt = f"""
            你是一名資深雅思官方考官。請根據官方四大評分標準嚴格批改以下作文：
            題目：{st.session_state.question}
            學生作文：{user_essay}

            你必須輸出符合以下鍵值的純 JSON 結構：
            - overall_band (float): 總分 (例如 6.5)
            - tr_score (float): Task Response 得分
            - tr_feedback (string): Task Response 繁體中文具體診斷
            - cc_score (float): Coherence and Cohesion 得分
            - cc_feedback (string): Coherence and Cohesion 繁體中文具體診斷
            - lr_score (float): Lexical Resource 得分
            - lr_feedback (string): Lexical Resource 繁體中文具體診斷
            - gra_score (float): Grammatical Range and Accuracy 得分
            - gra_feedback (string): Grammatical Range and Accuracy 繁體中文具體診斷
            - corrections (list of objects): 包含 original (原文句), improved (潤飾句), reason (修改說明)
            - model_revision (string): 針對此題目的 8.0+ 範文改寫
            """
            
            try:
                # 使用 response_mime_type 強制原生 JSON 模式
                response = generate_with_retry(client,
    model="gemini-3.8-flash",
    contents=grading_prompt,
    config=types.GenerateContentConfig(
        response_mime_type="application/json",
        temperature=0.2
    )
)
                
                result = json.loads(response.text)

                # 批改成功後才扣次數
                increment_usage(active_student_id)

                st.subheader(f"🏆 預估總分：Band {result.get('overall_band', 'N/A')}")
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Task Response", result.get("tr_score", "-"))
                col2.metric("Coherence", result.get("cc_score", "-"))
                col3.metric("Lexical", result.get("lr_score", "-"))
                col4.metric("Grammar", result.get("gra_score", "-"))

                st.divider()
                st.write("### 📌 四項標準詳細診斷")
                st.write(f"- **TR 審題與論點：** {result.get('tr_feedback', '')}")
                st.write(f"- **CC 連貫與銜接：** {result.get('cc_feedback', '')}")
                st.write(f"- **LR 字彙豐富度：** {result.get('lr_feedback', '')}")
                st.write(f"- **GRA 句型與文法：** {result.get('gra_feedback', '')}")

                st.divider()
                st.write("### ✏️ 重點句子潤飾與修正")
                for item in result.get("corrections", []):
                    st.markdown(f"- **原文：** `{item.get('original', '')}`")
                    st.markdown(f"  **建議：** `{item.get('improved', '')}`")
                    st.caption(f"  *原因：{item.get('reason', '')}*")

                st.divider()
                st.write("### 🌟 考官示範改寫範文")
                st.write(result.get("model_revision", ""))

            except json.JSONDecodeError:
                st.error("模型輸出格式非合法 JSON，請再點擊一次重試！")
            except Exception as e:
                st.error(f"批改過程發生錯誤：{str(e)}")
