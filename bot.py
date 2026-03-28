import os
import logging
import json
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
AGENT_CHAT_ID  = os.getenv("AGENT_CHAT_ID")
SHEET_ID       = "11CaeW21ydjnHKrHAScI633FTItgBqGkPZEoJHUIBJa0"

genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """أنت مساعد عقاري اسمك "أحمد". تتكلم بالعربية بأسلوب إنساني دافئ وطبيعي تماماً.

قواعدك الذهبية:
1. لا تسأل أكثر من سؤال واحد في كل رسالة
2. ردودك قصيرة وطبيعية — جملتين أو ثلاث كحد أقصى
3. لا تبدو كبوت أبداً — تكلم كصديق خبير في العقار
4. إذا سأل عن السعر، اذكره ثم أضف نقطة جذب واحدة مغرية
5. إذا كان متردداً، أبرز الفرصة بدون ضغط
6. هدفك: تحويل الفضول إلى اهتمام حقيقي وجمع بيانات العميل بشكل طبيعي

معلومات تجمعها خلال المحادثة بشكل طبيعي:
- الاسم، رقم الهاتف، الميزانية، المنطقة، نوع العقار، وقت الشراء، التمويل البنكي

أسلوب الإقناع:
- للمتردد: "بصراحة، اللي يتردد كثير بتيجي فرص تفوته"
- للسائل عن السعر: السعر + ميزة + ندرة
- للمستعجل: تعامل سريع ومباشر

عندما تجمع الاسم + الهاتف + الميزانية اكتب في نهاية ردك: [LEAD_READY]"""

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_json = os.getenv("GOOGLE_CREDENTIALS")
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(SHEET_ID).sheet1

def calculate_priority(data: dict) -> str:
    score = 0
    budget = data.get("budget", "")
    if any(x in budget for x in ["نقد", "كاش", "جاهز", "كامل"]):
        score += 3
    elif any(x in budget for x in ["تقسيط", "بنك", "قرض"]):
        score += 1
    timeline = data.get("timeline", "")
    if any(x in timeline for x in ["الآن", "فوري", "هذا الشهر", "قريب"]):
        score += 3
    elif any(x in timeline for x in ["3", "ثلاث", "شهرين"]):
        score += 2
    elif any(x in timeline for x in ["6", "ست", "نصف"]):
        score += 1
    if score >= 6:
        return "🔴 عالية جداً — تواصل فوراً"
    elif score >= 4:
        return "🟠 عالية"
    elif score >= 2:
        return "🟡 متوسطة"
    else:
        return "🟢 منخفضة"

def save_lead(data: dict) -> str:
    try:
        sheet = get_sheet()
        priority = calculate_priority(data)
        row = [
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            data.get("name", "—"),
            data.get("phone", "—"),
            data.get("budget", "—"),
            data.get("area", "—"),
            data.get("property_type", "—"),
            data.get("financing", "—"),
            data.get("timeline", "—"),
            priority
        ]
        sheet.append_row(row)
        return priority
    except Exception as e:
        logger.error(f"خطأ Sheets: {e}")
        return "غير محدد"

def get_ai_response(conversation_history: list) -> str:
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT
        )
        # بناء السجل بطريقة صحيحة
        history = []
        for msg in conversation_history[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            history.append({"role": role, "parts": msg["content"]})

        chat = model.start_chat(history=history)
        last_msg = conversation_history[-1]["content"]
        response = chat.send_message(last_msg)
        return response.text
    except Exception as e:
        logger.error(f"خطأ Gemini: {e}")
        return f"عذراً، فيه مشكلة. ({str(e)[:50]})"

def extract_lead_data(conversation_history: list) -> dict:
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        full_convo = "\n".join([
            f"{'عميل' if m['role']=='user' else 'أحمد'}: {m['content']}"
            for m in conversation_history
        ])
        prompt = f"""من هذه المحادثة استخرج البيانات بصيغة JSON فقط:
{{"name":"","phone":"","budget":"","area":"","property_type":"","financing":"","timeline":""}}
المحادثة:
{full_convo}"""
        response = model.generate_content(prompt)
        text = response.text.strip().replace("```json","").replace("```","").strip()
        return json.loads(text)
    except Exception as e:
        logger.error(f"خطأ استخراج: {e}")
        return {}

async def notify_agent(context, lead_data, priority, user_id):
    try:
        msg = (
            f"🏠 *عميل جديد!*\n\n"
            f"*الأولوية: {priority}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 {lead_data.get('name','—')}\n"
            f"📱 `{lead_data.get('phone','—')}`\n"
            f"💰 {lead_data.get('budget','—')}\n"
            f"📍 {lead_data.get('area','—')}\n"
            f"🏡 {lead_data.get('property_type','—')}\n"
            f"⏰ {lead_data.get('timeline','—')}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 تم الحفظ في Sheets ✅"
        )
        await context.bot.send_message(chat_id=AGENT_CHAT_ID, text=msg, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"خطأ إشعار: {e}")

user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"history": [], "lead_sent": False}
    await update.message.reply_text(
        "أهلاً وسهلاً! 👋\n\n"
        "أنا أحمد، مساعدك العقاري الشخصي.\n"
        "شو اللي تدور عليه؟ 🏠"
    )

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text
    if user_id not in user_sessions:
        user_sessions[user_id] = {"history": [], "lead_sent": False}
    session = user_sessions[user_id]
    session["history"].append({"role": "user", "content": user_text})
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")
    ai_response = get_ai_response(session["history"])
    lead_ready = "[LEAD_READY]" in ai_response
    clean_response = ai_response.replace("[LEAD_READY]", "").strip()
    session["history"].append({"role": "assistant", "content": clean_response})
    await update.message.reply_text(clean_response)
    if lead_ready and not session["lead_sent"]:
        session["lead_sent"] = True
        lead_data = extract_lead_data(session["history"])
        priority = save_lead(lead_data)
        await notify_agent(context, lead_data, priority, user_id)

async def reset_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"history": [], "lead_sent": False}
    await update.message.reply_text("تم البدء من جديد! 🏠")

def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ البوت شغال!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
