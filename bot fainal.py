import os
import logging
import json
from datetime import datetime
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
import anthropic
import gspread
from google.oauth2.service_account import Credentials

TELEGRAM_TOKEN   = os.getenv("TELEGRAM_TOKEN")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
AGENT_CHAT_ID    = os.getenv("AGENT_CHAT_ID")
SHEET_ID         = "11CaeW21ydjnHKrHAScI633FTItgBqGkPZEoJHUIBJa0"

claude = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

SYSTEM_PROMPT = """أنت مساعد عقاري اسمك "أحمد". تتكلم بالعربية بأسلوب إنساني دافئ وطبيعي تماماً.

قواعدك الذهبية:
1. لا تسأل أكثر من سؤال واحد في كل رسالة
2. ردودك قصيرة وطبيعية — جملتين أو ثلاث كحد أقصى
3. لا تبدو كبوت أبداً — تكلم كصديق خبير في العقار
4. إذا سأل عن السعر، اذكره ثم أضف نقطة جذب واحدة مغرية
5. إذا كان متردداً، أبرز الفرصة بدون ضغط (مثل: "هذه آخر 3 وحدات متبقية")
6. هدفك: تحويل الفضول إلى اهتمام حقيقي وجمع بيانات العميل بشكل طبيعي

معلومات تجمعها خلال المحادثة بشكل طبيعي:
- الاسم (قل: "باسمك إيش أناديك؟")
- رقم الهاتف (قل: "عشان التواصل المباشر، رقمك كم؟")
- الميزانية (قل: "تقريباً كم ميزانيتك؟ نقد ولا تقسيط؟")
- المنطقة المفضلة
- نوع العقار (شقة/فيلا/تجاري)
- وقت الشراء
- هل عنده تمويل بنكي؟

أسلوب الإقناع الذكي:
- للمتردد: "بصراحة، اللي يتردد كثير بتيجي فرص تفوته. هذا المشروع عليه طلب كبير."
- للسائل عن السعر: السعر + ميزة + ندرة
- للمقارن: "الفرق الحقيقي هنا هو الموقع والسعر"
- للمستعجل: تعامل سريع ومباشر

عندما تجمع الاسم + الهاتف + الميزانية اكتب في نهاية ردك: [LEAD_READY]"""

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)

def get_sheet():
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive"
    ]
    creds_dict = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    gc = gspread.authorize(creds)
    return gc.open_by_key(SHEET_ID).sheet1

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
        sheet.append_row([
            datetime.now().strftime("%Y-%m-%d %H:%M"),
            data.get("name", "—"), data.get("phone", "—"),
            data.get("budget", "—"), data.get("area", "—"),
            data.get("property_type", "—"), data.get("financing", "—"),
            data.get("timeline", "—"), priority
        ])
        return priority
    except Exception as e:
        logger.error(f"خطأ Sheets: {e}")
        return "غير محدد"

def get_ai_response(conversation_history: list) -> str:
    try:
        messages = [
            {"role": m["role"], "content": m["content"]}
            for m in conversation_history
        ]
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=messages
        )
        return response.content[0].text
    except Exception as e:
        logger.error(f"خطأ Claude: {e}")
        return f"عذراً، فيه مشكلة. ({str(e)[:80]})"

def extract_lead_data(conversation_history: list) -> dict:
    try:
        full_convo = "\n".join([
            f"{'عميل' if m['role']=='user' else 'أحمد'}: {m['content']}"
            for m in conversation_history
        ])
        prompt = f"""من هذه المحادثة استخرج البيانات بصيغة JSON فقط بدون أي نص إضافي:
{{"name":"","phone":"","budget":"","area":"","property_type":"","financing":"","timeline":""}}
المحادثة:
{full_convo}"""
        response = claude.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text.strip().replace("```json","").replace("```","").strip()
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
