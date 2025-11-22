import streamlit as st
import openai
import edge_tts
import asyncio
import io
import base64
import re
from streamlit_mic_recorder import mic_recorder

# --- 1. CẤU HÌNH TRANG ---
st.set_page_config(page_title="Super Fast Voice Chat", page_icon="⚡")
st.title("⚡ Voice Chat: Streaming Real-time")

# --- 2. SIDEBAR CÀI ĐẶT ---
with st.sidebar:
    st.header("Cài đặt")
    api_key = st.text_input("Nhập OpenAI API Key:", type="password")
    voice_option = st.selectbox(
        "Chọn giọng đọc:",
        ["vi-VN-HoaiMyNeural", "vi-VN-NamMinhNeural"]
    )
    
    st.markdown("---")
    if st.button("🗑️ Xóa lịch sử chat"):
        st.session_state.messages = []
        st.rerun()

# Kiểm tra API Key
if not api_key:
    st.warning("⚠️ Vui lòng nhập OpenAI API Key ở thanh bên trái để bắt đầu.")
    st.stop()

client = openai.OpenAI(api_key=api_key)

# Khởi tạo lịch sử chat
if "messages" not in st.session_state:
    st.session_state.messages = []

# --- 3. JAVASCRIPT PLAYER (TRÁI TIM CỦA HỆ THỐNG) ---
# Tạo một trình phát âm thanh ẩn, tự động xếp hàng các đoạn audio được gửi xuống
def setup_audio_player():
    js_code = """
        <script>
            // Hàng đợi âm thanh
            window.audioQueue = [];
            window.isPlaying = false;

            // Hàm lấy audio từ hàng đợi và phát
            async function playNext() {
                if (window.audioQueue.length === 0) {
                    window.isPlaying = false;
                    return;
                }
                window.isPlaying = true;
                const audioData = window.audioQueue.shift();
                const audio = new Audio("data:audio/mp3;base64," + audioData);
                
                // Khi đoạn này hết, tự động gọi đoạn tiếp theo
                audio.onended = function() {
                    playNext();
                };
                
                try {
                    await audio.play();
                } catch (e) {
                    console.error("Autoplay blocked or error:", e);
                    window.isPlaying = false; 
                }
            }

            // Lắng nghe sự kiện từ Python gửi xuống
            window.parent.document.addEventListener('streamlit:play_chunk', function(e) {
                const b64 = e.detail.base64;
                window.audioQueue.push(b64);
                // Nếu chưa phát gì thì bắt đầu phát ngay
                if (!window.isPlaying) {
                    playNext();
                }
            });
        </script>
    """
    # Chèn JS vào trang (height=0 để ẩn)
    st.components.v1.html(js_code, height=0, width=0)

# Gọi setup mỗi lần app rerun
setup_audio_player()

# --- 4. CÁC HÀM XỬ LÝ LOGIC ---

def stream_audio_chunk_to_js(audio_bytes):
    """Chuyển bytes âm thanh thành Base64 và bắn sự kiện xuống JS"""
    b64 = base64.b64encode(audio_bytes).decode()
    js_trigger = f"""
        <script>
            var event = new CustomEvent('streamlit:play_chunk', {{ detail: {{ base64: "{b64}" }} }});
            window.parent.document.dispatchEvent(event);
        </script>
    """
    st.components.v1.html(js_trigger, height=0, width=0)

async def generate_audio_chunk(text, voice):
    """Tạo audio từ text bằng Edge TTS (Xử lý trong RAM)"""
    if not text or not text.strip():
        return None
    
    communicate = edge_tts.Communicate(text, voice)
    mp3_fp = io.BytesIO()
    
    async for chunk in communicate.stream():
        if chunk["type"] == "audio":
            mp3_fp.write(chunk["data"])
            
    return mp3_fp.getvalue()

def transcribe_audio(audio_bytes):
    """Speech-to-Text dùng OpenAI Whisper"""
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "voice.wav" # Whisper cần tên file giả lập
    try:
        transcript = client.audio.transcriptions.create(
            model="whisper-1", 
            file=audio_file, 
            language="vi"
        )
        return transcript.text
    except Exception as e:
        st.error(f"Lỗi STT: {e}")
        return None

# --- 5. GIAO DIỆN & MAIN LOOP ---

# Hiển thị nút ghi âm
st.markdown("### 🎙️ Bấm nút bên dưới để nói chuyện")
col1, col2 = st.columns([1, 5])
with col1:
    audio_input = mic_recorder(
        start_prompt="Ghi âm",
        stop_prompt="Dừng & Gửi",
        just_once=True,
        key='recorder'
    )

# Xử lý khi có âm thanh
if audio_input:
    # A. Transcribe
    user_text = transcribe_audio(audio_input['bytes'])
    
    if user_text:
        # B. Hiển thị User Text
        st.session_state.messages.append({"role": "user", "content": user_text})
        with st.chat_message("user"):
            st.write(user_text)
        
        # C. Xử lý AI & Streaming
        with st.chat_message("assistant"):
            message_placeholder = st.empty()
            full_response = ""
            current_sentence = ""
            
            # Gọi GPT-4o-mini với chế độ stream
            stream = client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": "Bạn là trợ lý AI, trả lời ngắn gọn, tự nhiên, thân thiện."},
                    *st.session_state.messages
                ],
                stream=True,
            )
            
            # Vòng lặp xử lý từng token (chữ) AI sinh ra
            for chunk in stream:
                if chunk.choices[0].delta.content:
                    token = chunk.choices[0].delta.content
                    full_response += token
                    current_sentence += token
                    
                    # Cập nhật text trên màn hình
                    message_placeholder.markdown(full_response + "▌")
                    
                    # Kiểm tra dấu câu để ngắt câu (. ! ? hoặc xuống dòng)
                    if re.search(r'[.!?\n]', token):
                        # Dọn dẹp câu (xóa khoảng trắng thừa)
                        text_to_speak = current_sentence.strip()
                        if text_to_speak:
                            # Tạo audio cho câu này
                            audio_chunk = asyncio.run(generate_audio_chunk(text_to_speak, voice_option))
                            if audio_chunk:
                                # Đẩy xuống hàng đợi phát nhạc
                                stream_audio_chunk_to_js(audio_chunk)
                        
                        # Reset câu hiện tại
                        current_sentence = ""

            # Xử lý phần dư còn lại (nếu AI không kết thúc bằng dấu câu)
            if current_sentence.strip():
                audio_chunk = asyncio.run(generate_audio_chunk(current_sentence, voice_option))
                if audio_chunk:
                    stream_audio_chunk_to_js(audio_chunk)
            
            # Hiển thị bản chốt cuối cùng
            message_placeholder.markdown(full_response)
            st.session_state.messages.append({"role": "assistant", "content": full_response})

# Hiển thị lịch sử chat bên dưới (trừ tin nhắn mới nhất đã hiện ở trên)
if len(st.session_state.messages) > 2:
    st.markdown("---")
    st.caption("Lịch sử hội thoại cũ:")
    for msg in st.session_state.messages[:-2]:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])
