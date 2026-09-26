import streamlit as st
from google import genai
import json
import sqlite3
from datetime import date, datetime

st.set_page_config(page_title="雅思寫作批改系統", layout="wide")
st.title("✍️ 雅思寫作 Task 2 智能出題與批改系統")

# ===========================
# 0. 資料庫設定：學員名冊與每日額度追蹤
# ===========================
DB_FILE = "usage_tracker.db"

def init_db():
    """初始化學員資料表與使用次數記錄表"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    # 學員名冊表
    c.execute('''
        CREATE TABLE IF NOT EXISTS students (
            student_id TEXT PRIMARY KEY,
            name TEXT,
            created_at TEXT
        )
    ''')
    # 每日使用額度記錄表
    c.execute('''
        CREATE TABLE IF NOT EXISTS usage (
            student_id TEXT,
            date TEXT,
            count INTEGER,
            PRIMARY KEY (student_id, date)
        )
    ''')
    conn.commit()
    conn.close()

def is_student_registered(student_id: str) -> bool:
    """檢查學號是否已註冊"""
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT 1 FROM students WHERE student_id = ?', (student_id,))
    row = c.fetchone()
    conn.close()
    return row is not None

def register_student(student_id: str, name: str) -> bool:
    """註冊新學員"""
    try:
        conn = sqlite3.connect(DB_FILE)
        c = conn.cursor()
        c.execute(
            'INSERT INTO students (student_id, name, created_at) VALUES (?, ?, ?)',
            (student_id, name, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        )
        conn.commit()
        conn.close()
        return True
    except sqlite3.IntegrityError:
        return False

def get_today_usage(student_id: str) -> int:
    """取得特定學員當日已批改次數"""
    today_str = date.today().isoformat()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('SELECT count FROM usage WHERE student_id = ? AND date = ?', (student_id, today_str))
    row = c.fetchone()
    conn.close()
    return row[0] if row else 0

def increment_usage(student_id: str):
    """批改成功後次數 +1"""
    today_str = date.today().isoformat()
    conn = sqlite3.connect(DB_FILE)
    c = conn.cursor()
    c.execute('''
        INSERT INTO usage (student_id, date, count)
        VALUES (?, ?, 1)
        ON CONFLICT(student_id, date)
        DO UPDATE SET count = count + 1
    ''', (student_id, today_str))
    conn.commit()
    conn.close()

init_db()

# ===========================
# 1. 側邊欄設定：登入 / 註冊 與 API Key 驗證
# ===========================
DAILY_LIMIT = 3  # 每位學員每日上限

# 取得 API Key（優先讀取 Secrets）
api_key = st.secrets.get("GEMINI_API_KEY", "")
if not api_key:
    api_key = st.sidebar.text_input("Gemini API Key（管理員設定）：", type="password")

if not api_key:
    st.warning("⚠️ 系統尚未設定 API Key，請聯繫管理員！")
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
            st.sidebar.error(f"學號 `{new_id}` 已被使用，請更換一個！")
        else:
            if register_student(new_id, new_name):
                st.sidebar.success(f"🎉 建立成功！學號：`{new_id}`。請切換至「登入系統」開始使用。")
            else:
                st.sidebar.error("建立失敗，請稍後重試。")
    st.stop()

else:  # 登入模式
    login_id = st.sidebar.text_input("請輸入學員編號：", placeholder="例如：ielts001").strip().lower()
    
    if not login_id:
        st.info("👈 請在左側欄位輸入【學員編號】登入；若是新同學請點選「註冊新學號」。")
        st.stop()
        
    if not is_student_registered(login_id):
        st.sidebar.error("❌ 找不到此學號，請確認輸入或前往「註冊新學號」！")
        st.stop()
        
    active_student_id = login_id

# 顯示已登入學員的額度
used_count = get_today_usage(active_student_id)
remaining_count = max(0, DAILY_LIMIT - used_count)

st.sidebar.markdown("---")
st.sidebar.markdown(f"**目前登入：** `{active_student_id}`")
st.sidebar.metric("今日剩餘批改次數", f"{remaining_count} / {DAILY_LIMIT}")

if remaining_count == 0:
    st.sidebar.error("❌ 今日批改額度已用完（每日上限 3 篇），明天將自動重置！")

# ===========================
# 2. 功能區：隨機出題
# ===========================
if "question" not in st.session_state:
    st.session_state.question = "Some people believe that university education should be free for everyone. To what extent do you agree or disagree?"

if st.button("🎲 隨機產生雅思 Task 2 題目"):
    with st.spinner("正在為您出題..."):
        prompt_q = "請隨機產生一道雅思 Task 2 題目，只需輸出英文題目本身，不要其他文字。"
        res = client.models.generate_content(model="gemini-2.5-flash", contents=prompt_q)
        st.session_state.question = res.text.strip()

st.info(f"**當前題目：**\n\n{st.session_state.question}")

# ===========================
# 3. 功能區：學生作文輸入
# ===========================
user_essay = st.text_area("請在此貼上或輸入你的作文（建議 250 字以上）：", height=250)
word_count = len(user_essay.split()) if user_essay else 0
st.caption(f"目前字數：{word_count} 字")

# ===========================
# 4. 功能區：評分批改
# ===========================
submit_disabled = (remaining_count <= 0)

if st.button("🚀 開始批改與評分", disabled=submit_disabled):
    if remaining_count <= 0:
        st.error("您今日的批改額度已用完，明天將自動重置！")
    elif word_count < 50:
        st.error("文章字數過少，請至少輸入 50 字以上再進行批改！")
    else:
        with st.spinner("雅思考官正在依官方四項標準評分中，請稍候..."):
            grading_prompt = f"""
            你是一名資深雅思官方考官。請根據官方四大評分標準批改以下作文：
            題目：{st.session_state.question}
            學生作文：{user_essay}

            請嚴格以 JSON 格式回傳，格式必須如下：
            {{
              "overall_band": 6.5,
              "tr_score": 6.5,
              "tr_feedback": "Task Achievement 簡評",
              "cc_score": 6.0,
              "cc_feedback": "Coherence and Cohesion 簡評",
              "lr_score": 6.5,
              "lr_feedback": "Lexical Resource 簡評",
              "gra_score": 6.0,
              "gra_feedback": "Grammatical Range and Accuracy 簡評",
              "corrections": [
                {{"original": "錯誤原文句", "improved": "建議修改句", "reason": "修改原因"}}
              ],
              "model_revision": "針對此文章的高分示範改寫"
            }}
            只回傳純 JSON，不要任何 Markdown 標記（如 ```json）。
            """
            
            try:
                response = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=grading_prompt
                )
                raw_text = response.text.strip().replace("```json", "").replace("```", "")
                result = json.loads(raw_text)

                # 批改成功，次數 +1
                increment_usage(active_student_id)

                st.subheader(f"🏆 預估總分：Band {result['overall_band']}")
                
                col1, col2, col3, col4 = st.columns(4)
                col1.metric("Task Response", result["tr_score"])
                col2.metric("Coherence", result["cc_score"])
                col3.metric("Lexical", result["lr_score"])
                col4.metric("Grammar", result["gra_score"])

                st.divider()
                st.write("### 📌 四項標準詳細診斷")
                st.write(f"- **TR 審題與論點：** {result['tr_feedback']}")
                st.write(f"- **CC 連貫與銜接：** {result['cc_feedback']}")
                st.write(f"- **LR 字彙豐富度：** {result['lr_feedback']}")
                st.write(f"- **GRA 句型與文法：** {result['gra_feedback']}")

                st.divider()
                st.write("### ✏️ 重點句子潤飾與修正")
                for item in result.get("corrections", []):
                    st.markdown(f"- **原文：** `{item['original']}`")
                    st.markdown(f"  **建議：** `{item['improved']}`")
                    st.caption(f"  *原因：{item['reason']}*")

                st.divider()
                st.write("### 🌟 考官示範改寫範文")
                st.write(result["model_revision"])

            except Exception as e:
                st.error(f"批改解析發生錯誤，請重試一次。錯誤訊息：{e}")