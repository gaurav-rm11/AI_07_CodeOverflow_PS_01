import re
from time import time
from flask import Flask, request, jsonify
from dotenv import load_dotenv
from flask_cors import CORS
import os
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import SecretStr
from PIL import Image
import pytesseract
import io
import chromadb
from chromadb.config import Settings
import shutil
import requests
from create_knoweldge_base import create_knowledge_base_fn
from fetch_from_knoweldge_base import fetch_from_knowledge_base
import json
import base64
import cv2
import numpy as np
from deepface import DeepFace
from collections import Counter
import time
from threading import Lock
from flask import session



app = Flask(__name__)
CORS(app,supports_credentials=True)
load_dotenv()
GOOGLE_GEMINI_API_KEY = os.getenv("GOOGLE_GEMINI_API_KEY")
emotion_frames = {}  
emotion_locks = {}   

@app.route('/chat', methods=['POST'])
def chat():
    user_input=request.json.get("user_input", "") # type: ignore
    if user_input:
        model=ChatGoogleGenerativeAI(model="gemini-2.0-flash",api_key=SecretStr(GOOGLE_GEMINI_API_KEY) if GOOGLE_GEMINI_API_KEY else None)
        result = model.invoke(user_input).content
        cleaned_result = re.sub(r'(\*\*|\*|\n\n|\n)', '', str(result))
        return jsonify({"response": cleaned_result})  
    else:
        return jsonify({"response": "Please provide user input"})


pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
@app.route('/ocr', methods=['POST'])
def ocr():
    if 'image' not in request.files:
        return jsonify({'error': 'No image part in request'}), 400

    image_file = request.files['image']
    
    if image_file.filename == '':
        return jsonify({'error': 'No selected image'}), 400

    image = Image.open(image_file.stream)
    text = pytesseract.image_to_string(image)
    
    return jsonify({'text': text})
    

@app.route('/update_knowledge_base', methods=['POST'])
def update_knowledge_base():
    try:
        if 'pdf' not in request.files:
            return jsonify({'error': 'No PDF file in request'}), 400

        pdf_file = request.files['pdf']
        
        if pdf_file.filename == '':
            return jsonify({'error': 'No selected PDF file'}), 400
        
     
        current_dir = os.path.dirname(os.path.abspath(__file__))
        db_dir = os.path.join(current_dir, "db")
        pdf_path = os.path.join(db_dir, "hack-faq.pdf")
        persistent_directory = os.path.join(db_dir, "chroma_db")
    
        os.makedirs(db_dir, exist_ok=True)
    
        if os.path.exists(pdf_path):
            os.remove(pdf_path)
            print(f"Removed existing PDF file: {pdf_path}")
        
        pdf_file.save(pdf_path)
        print(f"Saved new PDF file to: {pdf_path}")
        
        
        if os.path.exists(persistent_directory):
            shutil.rmtree(persistent_directory)
            print(f"Removed existing vector store directory: {persistent_directory}")
    
        success = create_knowledge_base_fn()
        
        if success:
            return jsonify({
                'status': 'success', 
                'message': 'Knowledge base updated successfully'
            })
        else:
            return jsonify({
                'status': 'error', 
                'message': 'Failed to update knowledge base'
            }), 500
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'Error updating knowledge base: {str(e)}'
        }), 500


MY_PROMPT="""
You are an intelligent and professional virtual assistant for the IDMS ERP System, designed specifically for manufacturing industries. Your role is to act as a knowledgeable help desk, assisting users with queries related to Sales, Purchase, Inventory, Production, Quality Control, Dispatch, Finance, and GST compliance.

Your responses must be clear, concise, and structured based on the IDMS ERP database. When answering queries, please follow these guidelines:

1. Provide precise and informative answers, avoiding unnecessary details.
2. Refer to relevant ERP modules, transactions, reports, and dependencies where applicable.
3. Offer step-by-step guidance for using ERP functionalities.
4. Explain GST compliance rules and their implementation in IDMS, including invoices, returns, and reconciliation.
5. Troubleshoot common user issues within the system.

Your output must always be structured in JSON format as follows:

{
  "response_code": "200",
  "content": "Your detailed response goes here, answering the user's query.",
  "module_reference": "Relevant ERP module name (if applicable)",
  "related_transactions": ["List of relevant transactions"],
  "suggested_reports": ["List of relevant reports"]
}

Handling Security & Inappropriate Queries:
If a user asks a security-sensitive question (e.g., access credentials, hacking attempts) or an inappropriate question (e.g., offensive language, unrelated topics), respond in the following format:

{
  "response_code": "403",
  "content": "Your query violates security or ethical guidelines. Please ask a relevant question related to IDMS ERP.",
  "module_reference": null,
  "related_transactions": [],
  "suggested_reports": []
}

Guiding the User for Better Queries:
If a user query is vague, ask for clarification before responding using this format:

{
  "response_code": "422",
  "content": "Could you please specify which module or process you are referring to? This will help me provide a precise answer.",
  "module_reference": null,
  "related_transactions": [],
  "suggested_reports": []
}

Response Code Legend:
- 200 → Success (Valid query, response provided)
- 403 → Forbidden (Security-related or inappropriate query)
- 422 → Unprocessable (Query is vague and needs clarification)

Always maintain a friendly, professional, and solution-oriented tone. When a user asks about a process (e.g., "How do I generate a GST invoice?"), explain it step by step. When a user asks for insights (e.g., "How does IDMS handle stock aging?"), provide the relevant reports along with their purpose.

Prioritize accuracy and efficiency in resolving queries. Your structured responses with response codes will help the chatbot system integrate automated actions, improve debugging, and streamline logging.

End of prompt.
"""

@app.route('/chatting', methods=['POST'])
def chatting():
    user_input = request.json.get("user_input", "")  # type: ignore

    if not user_input:
        return jsonify({"response": "Please provide user input"})
    
    try:

        docs = fetch_from_knowledge_base(user_input)
        
        if not docs or len(docs) == 0:
           
            model = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                api_key=SecretStr(GOOGLE_GEMINI_API_KEY) if GOOGLE_GEMINI_API_KEY else None
            )
            full_prompt = f"{MY_PROMPT}\n\nUser Query: {user_input}\n\nProvide a response in the JSON format specified above."
            result = model.invoke(full_prompt).content
            cleaned_result = clean_text_content(str(result))
            
            try:
                parsed_result = json.loads(cleaned_result)
            except Exception:
                parsed_result = {
                    "response_code": "200",
                    "content": cleaned_result,
                    "module_reference": None,
                    "related_transactions": [],
                    "suggested_reports": []
                }
            return jsonify({"response": parsed_result, "source_docs": []})
        
        doc_contents = [clean_text_content(doc.page_content) for doc in docs]
        doc_sources = [doc.metadata.get('source', 'Unknown') if doc.metadata else 'Unknown' for doc in docs]
        
       
        formatted_docs = '\n\n'.join(doc_contents)
        
        enhanced_prompt = f"""
Based on the following information from our knowledge base:
{'-' * 30}
{formatted_docs}
{'-' * 30}

Please answer the user's query: "{user_input}"

Use only the information provided above to answer the query. If the information is not sufficient 
to provide a complete answer, please state what is known from the provided context and indicate 
what information is missing.
"""

        model = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            api_key=SecretStr(GOOGLE_GEMINI_API_KEY) if GOOGLE_GEMINI_API_KEY else None
        )
        result = model.invoke(enhanced_prompt).content
        cleaned_result = clean_text_content(str(result))
        
        try:
            result_json = json.loads(cleaned_result)
        except Exception:
            result_json = {
                "response_code": "200",
                "content": cleaned_result,
                "module_reference": None,
                "related_transactions": [],
                "suggested_reports": []
            }
            
        
        clean_doc_contents = [clean_text_content(doc.page_content) for doc in docs]
        
        response_data = {
            "response": result_json,
            "source_docs": [
                {"content": clean_doc_contents[i], "source": doc_sources[i]} for i in range(len(clean_doc_contents))
            ]
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        return jsonify({"response": {"response_code": "500", "content": f"An error occurred: {str(e)}", "module_reference": None, "related_transactions": [], "suggested_reports": []}, "source_docs": []})


def clean_text_content(text):
    
    code_block_pattern = r'```(?:json)?\s*(\{[\s\S]*?\})\s*```'
    code_match = re.search(code_block_pattern, text)
    if code_match:
      
        json_content = code_match.group(1)
       
        json_content = json_content.replace('\\"', '"')
        return json_content


    cleaned = text.replace('\\n', ' ').replace('\\t', ' ')
    cleaned = re.sub(r'\*\*|\*', '', cleaned)
    cleaned = re.sub(r'\s+', ' ', cleaned)
    
    
    cleaned = cleaned.replace('\\"', '"')  
    cleaned = cleaned.replace('\\\\', '\\')  
    
    cleaned = cleaned.replace('\\*', '•')
    cleaned = cleaned.replace('\\r', ' ')
    cleaned = re.sub(r'\\([^"\\])', r'\1', cleaned)
    
    return cleaned.strip()

DID_API_KEY = os.getenv("MYYMYY")
headers = {
    "Authorization": f"Basic {DID_API_KEY}",
    "Content-Type": "application/json"
}

@app.route('/generate-video', methods=['POST'])
def generate_and_fetch_video():
    try:
       
        data = request.json
        input_text = data.get('text', """ 
Hey!
Great to meet you — I’m your assistant, here to help you turn your ideas into reality.
Whether it’s building a cool project, figuring out a pipeline, or just exploring new tech, I’ve got your back.
So, what are we working on today?
""")# type: ignore
        
        source_url = data.get("source_url", "https://cdn.getmerlin.in/cms/img_AQO_Pe_Pie_STC_59p_Oy_Zo8mbm7d_5a6a9d88fe.png") # type: ignore

        payload = {
            "source_url": source_url,
            "script": {
                "type": "text",
                "input": input_text
            }
        }

        post_response = requests.post("https://api.d-id.com/talks", headers=headers, json=payload)

        if post_response.status_code != 201:
            return jsonify({"error": "Failed to start video generation", "details": post_response.text}), post_response.status_code

        talk_id = post_response.json().get("id")

        get_url = f"https://api.d-id.com/talks/{talk_id}"

        for _ in range(30): 
            get_response = requests.get(get_url, headers=headers)
            if get_response.status_code == 200:
                result = get_response.json()
                status = result.get("status")
                if status == "done":
                    video_url = result.get("result_url")
                    return jsonify({
                        "message": "✅ Video generated successfully!",
                        "video_url": video_url,
                        "talk_id": talk_id
                    })
                elif status == "error":
                    return jsonify({"error": "❌ Video generation failed"}), 500
                else:
                    time.sleep(2)  
            else:
                return jsonify({"error": "⚠️ Failed to check video status", "details": get_response.text}), get_response.status_code

        return jsonify({"error": "⏳ Timeout: Video generation took too long"}), 504

    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/image-chat', methods=['POST'])
def image_chat():
    try:
        if 'image' not in request.files:
            return jsonify({
                "response": {
                    "response_code": "422", 
                    "content": "Please provide an image file", 
                    "module_reference": None, 
                    "related_transactions": [], 
                    "suggested_reports": []
                }, 
                "source_docs": []
            })

        image_file = request.files['image']
        user_input = request.form.get("user_input", "Analyze this image")
        
        if image_file.filename == '':
            return jsonify({
                "response": {
                    "response_code": "422", 
                    "content": "No selected image file", 
                    "module_reference": None, 
                    "related_transactions": [], 
                    "suggested_reports": []
                }, 
                "source_docs": []
            })

        image_content = image_file.read()
        image = Image.open(io.BytesIO(image_content))
        
        base64_image = base64.b64encode(image_content).decode('utf-8')
        
        from langchain_google_genai import ChatGoogleGenerativeAI
        
        model = ChatGoogleGenerativeAI(
            model="gemini-2.0-pro-exp-02-05",  
            api_key=SecretStr(GOOGLE_GEMINI_API_KEY) if GOOGLE_GEMINI_API_KEY else None
        )
        
        full_prompt = f"{MY_PROMPT}\n\nUser Query with Image: {user_input}\n\nProvide a response in the JSON format specified above based on the image content."
        
        try:
           
            text_content = pytesseract.image_to_string(image)
            
            if len(text_content) > 50:
                docs = fetch_from_knowledge_base(text_content)
                
                if docs and len(docs) > 0:
                    doc_contents = [clean_text_content(doc.page_content) for doc in docs]
                    doc_sources = [doc.metadata.get('source', 'Unknown') if doc.metadata else 'Unknown' for doc in docs]
                    formatted_docs = '\n\n'.join(doc_contents)
                    enhanced_prompt = f"""
Based on the following information from our knowledge base:
{'-' * 30}
{formatted_docs}
{'-' * 30}

Please analyze this image and answer the query: "{user_input}"

Consider both the image content and the knowledge base information in your response.
"""
                    # Fixed: Use the correct message format for Gemini
                    messages = [
                        {"role": "user", "content": enhanced_prompt},
                        {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
                    ]
                    
                    result = model.invoke(messages).content
                    
                    cleaned_result = clean_text_content(str(result))
                    
                    try:
                        result_json = json.loads(cleaned_result)
                    except Exception:
                        result_json = {
                            "response_code": "200",
                            "content": cleaned_result,
                            "module_reference": None,
                            "related_transactions": [],
                            "suggested_reports": []
                        }
                        
                    clean_doc_contents = [clean_text_content(doc.page_content) for doc in docs]
                    
                    response_data = {
                        "response": result_json,
                        "source_docs": [
                            {"content": clean_doc_contents[i], "source": doc_sources[i]} for i in range(len(clean_doc_contents))
                        ]
                    }
                    
                    return jsonify(response_data)
            
        except Exception as e:
            print(f"Error during knowledge base lookup: {str(e)}")
            
        messages = [
            {"role": "user", "content": full_prompt},
            {"role": "user", "content": [{"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}
        ]
        
        result = model.invoke(messages).content
        
        cleaned_result = clean_text_content(str(result))
        
        try:
            parsed_result = json.loads(cleaned_result)
        except Exception:
            parsed_result = {
                "response_code": "200",
                "content": cleaned_result,
                "module_reference": None,
                "related_transactions": [],
                "suggested_reports": []
            }
            
        return jsonify({"response": parsed_result, "source_docs": []})
        
    except Exception as e:
        import traceback
        traceback.print_exc()  
        return jsonify({
            "response": {
                "response_code": "500", 
                "content": f"An error occurred processing the image: {str(e)}", 
                "module_reference": None, 
                "related_transactions": [], 
                "suggested_reports": []
            }, 
            "source_docs": []
        })
        
@app.route('/analyze-frame', methods=['POST'])
def analyze_frame():
    
    try:
        if 'image' not in request.files:
            return jsonify({'error': 'No image part in request'}), 400

        image_file = request.files['image']
        session_id = request.form.get('session_id')
        token = request.form.get('token', 'continue')  
        user_input = request.form.get('user_input', '')  
        
        if not session_id:
            return jsonify({'error': 'Session ID is required'}), 400
        
        if image_file.filename == '':
            return jsonify({'error': 'No selected image'}), 400
        if session_id not in emotion_frames:
            emotion_frames[session_id] = []
            emotion_locks[session_id] = Lock()

        image_bytes = image_file.read()
        nparr = np.frombuffer(image_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        
        try:
            analysis = DeepFace.analyze(
                img_path=img,
                actions=['emotion'],
                enforce_detection=False, 
                detector_backend='opencv'  
            )
            
            if isinstance(analysis, list) and len(analysis) > 0:
                dominant_emotion = analysis[0]['dominant_emotion']
                emotion_score = analysis[0]['emotion'][dominant_emotion]
            else:
                dominant_emotion = 'unknown'
                emotion_score = 0
                
        except Exception as e:
            print(f"Error in emotion detection: {e}")
            dominant_emotion = 'unknown'
            emotion_score = 0
        
        with emotion_locks[session_id]:
            emotion_frames[session_id].append({
                'emotion': dominant_emotion,
                'score': emotion_score,
                'timestamp': time.time()
            })
            
            if len(emotion_frames[session_id]) > 20:  
                emotion_frames[session_id] = emotion_frames[session_id][-30:]
        
        if token == 'end' and user_input:
            return process_final_frame(session_id, user_input, img)
            
            
        return jsonify({
            'success': True,
            'session_id': session_id,
            'detected_emotion': dominant_emotion,
            'frame_processed': True
        })
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'message': f'Error analyzing frame: {str(e)}'
        }), 500

def process_final_frame(session_id, user_input, final_frame_img):
    try:

        with emotion_locks[session_id]:
            collected_emotions = emotion_frames[session_id].copy()
            emotion_frames[session_id] = []
        
        valid_emotions = [e['emotion'] for e in collected_emotions if e['emotion'] != 'unknown']
        
        if valid_emotions:
            emotion_counts = Counter(valid_emotions)
            dominant_emotion = emotion_counts.most_common(1)[0][0]
            confidence = emotion_counts[dominant_emotion] / len(valid_emotions)
        else:
            dominant_emotion = 'neutral'
            confidence = 1.0
            
        print(f"Final emotion assessment - {dominant_emotion} (confidence: {confidence:.2f})")
        
        emotion_context = {
            'happy': "The user appears to be in a positive mood. Use an encouraging and enthusiastic tone.",
            'sad': "The user appears sad. Use a supportive and empathetic tone.",
            'angry': "The user appears frustrated or angry. Use a calm and solution-focused tone.",
            'fear': "The user appears concerned or anxious. Use a reassuring tone and provide clear guidance.",
            'disgust': "The user appears dissatisfied. Address their concerns professionally and offer solutions.",
            'surprise': "The user appears surprised. Provide thorough explanations.",
            'neutral': "The user appears neutral. Use a balanced, informative tone."
        }
        
        emotion_guidance = emotion_context.get(dominant_emotion, emotion_context['neutral'])
        
        docs = fetch_from_knowledge_base(user_input)
        
        if not docs or len(docs) == 0:
            model = ChatGoogleGenerativeAI(
                model="gemini-2.0-flash",
                api_key=SecretStr(GOOGLE_GEMINI_API_KEY) if GOOGLE_GEMINI_API_KEY else None
            )
            
            full_prompt = f"""{MY_PROMPT}

User Query: {user_input}

User's Emotional State: {dominant_emotion}
Emotional Guidance: {emotion_guidance}

Provide a response in the JSON format specified above, adapting your tone to match the user's emotional state.
"""
            
            result = model.invoke(full_prompt).content
            cleaned_result = clean_text_content(str(result))
            
            try:
                parsed_result = json.loads(cleaned_result)
                
                parsed_result['detected_emotion'] = dominant_emotion
                parsed_result['emotion_confidence'] = confidence
                
            except Exception:
                parsed_result = {
                    "response_code": "200",
                    "content": cleaned_result,
                    "module_reference": None,
                    "related_transactions": [],
                    "suggested_reports": [],
                    "detected_emotion": dominant_emotion,
                    "emotion_confidence": confidence
                }
                
            return jsonify({
                "response": parsed_result, 
                "source_docs": [],
                "emotion_analysis": {
                    "dominant_emotion": dominant_emotion,
                    "confidence": confidence,
                    "emotion_counts": dict(emotion_counts) if valid_emotions else {"neutral": 1}
                }
            })
        
        doc_contents = [clean_text_content(doc.page_content) for doc in docs]
        doc_sources = [doc.metadata.get('source', 'Unknown') if doc.metadata else 'Unknown' for doc in docs]
        
        formatted_docs = '\n\n'.join(doc_contents)
        
        enhanced_prompt = f"""
Based on the following information from our knowledge base:
{'-' * 30}
{formatted_docs}
{'-' * 30}

Please answer the user's query: "{user_input}"

User's Emotional State: {dominant_emotion}
Emotional Guidance: {emotion_guidance}

Use only the information provided above to answer the query, while adapting your tone to match the user's emotional state.
If the information is not sufficient to provide a complete answer, please state what is known from the provided context 
and indicate what information is missing.
"""

        model = ChatGoogleGenerativeAI(
            model="gemini-2.0-flash",
            api_key=SecretStr(GOOGLE_GEMINI_API_KEY) if GOOGLE_GEMINI_API_KEY else None
        )
        
        result = model.invoke(enhanced_prompt).content
        cleaned_result = clean_text_content(str(result))
        
        try:
            result_json = json.loads(cleaned_result)
            
            result_json['detected_emotion'] = dominant_emotion
            result_json['emotion_confidence'] = confidence
            
        except Exception:
            result_json = {
                "response_code": "200",
                "content": cleaned_result,
                "module_reference": None,
                "related_transactions": [],
                "suggested_reports": [],
                "detected_emotion": dominant_emotion,
                "emotion_confidence": confidence
            }
        
        clean_doc_contents = [clean_text_content(doc.page_content) for doc in docs]
        
        response_data = {
            "response": result_json,
            "source_docs": [
                {"content": clean_doc_contents[i], "source": doc_sources[i]} for i in range(len(clean_doc_contents))
            ],
            "emotion_analysis": {
                "dominant_emotion": dominant_emotion,
                "confidence": confidence,
                "emotion_counts": dict(emotion_counts) if valid_emotions else {"neutral": 1}
            }
        }
        
        return jsonify(response_data)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            "response": {
                "response_code": "500", 
                "content": f"An error occurred: {str(e)}", 
                "module_reference": None, 
                "related_transactions": [], 
                "suggested_reports": [],
                "detected_emotion": "unknown"
            },
            "source_docs": [],
            "emotion_analysis": {
                "error": str(e)
            }
        }), 500



@app.before_request
def cleanup_old_sessions():
    try:
        current_time = time.time()
        sessions_to_remove = []
        
        for session_id, lock in emotion_locks.items():
            if lock.acquire(blocking=False):
                try:
                    if session_id in emotion_frames and emotion_frames[session_id]:
                        last_frame_time = max(frame['timestamp'] for frame in emotion_frames[session_id])
                        
                       
                        if current_time - last_frame_time > 1800:  
                            sessions_to_remove.append(session_id)
                    else:
                       
                        sessions_to_remove.append(session_id)
                finally:
                    lock.release()
        
        for session_id in sessions_to_remove:
            if session_id in emotion_frames:
                del emotion_frames[session_id]
            if session_id in emotion_locks:
                del emotion_locks[session_id]
    except Exception as e:
        print(f"Error during session cleanup: {e}")
    
    
if __name__ == "__main__":
    app.run(debug=True)
      
    