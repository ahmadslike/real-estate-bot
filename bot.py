import os
import logging
import json
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import google.generativeai as genai
import gspread
from google.oauth2.service_account import Credentials

# ==========================================
# الإعدادات
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "ضع_توكن_البوت_الجديد_هنا")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "AIzaSyDr-KRK257V_deJ97GlbRDOJHc0j9oona0")
AGENT_CHAT_ID  = os.getenv("AGENT_CHAT_ID",  "8280587120")
SHEET_ID       = "11CaeW21ydjnHKrHAScI633FTItgBqGkPZEoJHUIBJa0"

# ==========================================
# إعداد Gemini
# ==========================================
genai.configure(api_key=GEMINI_API_KEY)

SYSTEM_PROMPT = """أنت مساعد عقاري اسمك "أحمد". تتكلم بالعربية بأسلوب إنساني دافئ وطبيعي تماماً.

قواعدك الذهبية:
1. لا تسأل أكثر من سؤال واحد في كل رسالة
2. ردودك قصيرة وطبيعية — جملتين أو ثلاث كحد أقصى
3. لا تبدو كبوت أبداً — تكلم كصديق خبير في العقار
4. إذا سأل عن السعر، اذكره ثم أضف نقطة جذب واحدة مغرية
5. إذا كان متردداً، أبرز الفرصة بدون ضغط (مثل: "هذه آخر 3 وحدات متبقية")
6. هدفك: تحويل الفضول إلى اهتمام حقيقي، وجمع بيانات العميل بشكل طبيعي

معلومات تجمعها خلال المحادثة بشكل طبيعي (مش كاستمارة):
- الاسم (قل: "باسمك إيش أناديك؟")
- رقم الهاتف (قل: "عشان التواصل المباشر، رقمك كم؟")
- الميزانية (قل: "تقريباً كم ميزانيتك؟ نقد ولا تقسيط؟")
- المنطقة المفضلة
- نوع العقار (شقة/فيلا/تجاري)
- وقت الشراء (متى تريد الانتقال أو الشراء؟)
- هل عنده تمويل بنكي؟

أسلوب الإقناع الذكي:
- للمتردد: "بصراحة، اللي يتردد كثير بتيجي فرص تفوته. هذا المشروع عليه طلب كبير."
- للسائل عن السعر: اذكر السعر + ميزة + ندرة ("السعر X، وهي من آخر 3 وحدات")
- للمقارن: "الفرق الحقيقي هنا هو الموقع والسعر، غير كده ما رح تلاقي مثله"
- للمستعجل: تعامل سريع ومباشر

عندما تجمع الاسم + الهاتف + الميزانية أرسل في آخر ردك بالضبط: [LEAD_READY]
هذا للنظام فقط، لا يراه العميل."""

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==========================================
# Google Sheets
# ==========================================
def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
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
    if any(x in timeline for x in ["الآن", "فوري", "هذا الشهر", "قريب", "حالاً"]):
        score += 3
    elif any(x in timeline for x in ["3", "ثلاث", "شهرين"]):
        score += 2
    elif any(x in timeline for x in ["6", "ست", "نصف"]):
        score += 1

    financing = data.get("financing", "")
    if any(x in financing for x in ["موافق", "معتمد", "جاهز", "عندي"]):
        score += 2

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
        logger.error(f"خطأ في حفظ البيانات: {e}")
        return "غير محدد"

# ==========================================
# Gemini AI
# ==========================================
def get_ai_response(conversation_history: list) -> str:
    try:
        model = genai.GenerativeModel(
            model_name="gemini-1.5-flash",
            system_instruction=SYSTEM_PROMPT
        )
        gemini_history = []
        for msg in conversation_history[:-1]:
            role = "user" if msg["role"] == "user" else "model"
            gemini_history.append({"role": role, "parts": [msg["content"]]})

        chat = model.start_chat(history=gemini_history)
        response = chat.send_message(conversation_history[-1]["content"])
        return response.text
    except Exception as e:
        logger.error(f"خطأ في Gemini: {e}")
        return "عذراً، فيه مشكلة بسيطة. ممكن تعيد رسالتك؟"

def extract_lead_data(conversation_history: list) -> dict:
    try:
        model = genai.GenerativeModel("gemini-1.5-flash")
        full_convo = "\n".join([
            f"{'عميل' if m['role']=='user' else 'أحمد'}: {m['content']}"
            for m in conversation_history
        ])
        prompt = f"""من هذه المحادثة استخرج البيانات بصيغة JSON فقط بدون أي نص إضافي:
{{
  "name": "",
  "phone": "",
  "budget": "",
  "area": "",
  "property_type": "",
  "financing": "",
  "timeline": ""
}}

المحادثة:
{full_convo}"""

        response = model.generate_content(prompt)
        text = response.text.strip().replace("```json", "").replace("```", "").strip()
        return json.loads(text)
    except Exception as e:
        logger.error(f"خطأ في استخراج البيانات: {e}")
        return {}

# ==========================================
# إشعار السمسار
# ==========================================
async def notify_agent(context: ContextTypes.DEFAULT_TYPE, lead_data: dict, priority: str, user_id: int):
    try:
        msg = (
            f"🏠 *عميل جديد!*\n\n"
            f"*الأولوية: {priority}*\n"
            f"━━━━━━━━━━━━━━━\n"
            f"👤 الاسم: {lead_data.get('name', '—')}\n"
            f"📱 الهاتف: `{lead_data.get('phone', '—')}`\n"
            f"💰 الميزانية: {lead_data.get('budget', '—')}\n"
            f"📍 المنطقة: {lead_data.get('area', '—')}\n"
            f"🏡 نوع العقار: {lead_data.get('property_type', '—')}\n"
            f"🏦 التمويل: {lead_data.get('financing', '—')}\n"
            f"⏰ وقت الشراء: {lead_data.get('timeline', '—')}\n"
            f"━━━━━━━━━━━━━━━\n"
            f"📊 تم الحفظ في Google Sheets ✅"
        )
        await context.bot.send_message(
            chat_id=AGENT_CHAT_ID,
            text=msg,
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"خطأ في إشعار السمسار: {e}")

# ==========================================
# معالجة الرسائل
# ==========================================
user_sessions = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_sessions[user_id] = {"history": [], "lead_sent": False}
    welcome = (
        "أهلاً وسهلاً! 👋\n\n"
        "أنا أحمد، مساعدك العقاري الشخصي.\n"
        "سواء تبحث عن شقة، فيلا، أو فرصة استثمارية —\n"
        "أنا هنا أساعدك تلاقي اللي يناسبك بالضبط.\n\n"
        "شو اللي تدور عليه؟ 🏠"
    )
    await update.message.reply_text(welcome)

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
    await update.message.reply_text("تم البدء من جديد! شو اللي تدور عليه؟ 🏠")

# ==========================================
# تشغيل البوت
# ==========================================
def main():
    app = Application.builder().token(TELEGRAM_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    logger.info("✅ البوت العقاري شغال!")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
