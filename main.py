from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from motor.motor_asyncio import AsyncIOMotorClient
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import os
import json

# Load environment variables from .env file (for local development)
load_dotenv()

# Create FastAPI app
app = FastAPI()

# Fetch MongoDB URI from environment variables (set in Render.com or locally)
MONGODB_URI = os.getenv('MONGODB_URI')

# MongoDB connection
client = AsyncIOMotorClient(MONGODB_URI)
db = client['chat_db']
messages_user1 = db['messages_user_1']  # Collection for User1's messages
messages_user2 = db['messages_user_2']  # Collection for User2's messages

# Configure CORS for your frontend domain
frontend_url = os.getenv('FRONTEND_URL', '*')  # Default is '*' for development, but secure in production
app.add_middleware(
    CORSMiddleware,
    allow_origins=[frontend_url],  # Use environment variable to control CORS
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# WebSocket connection manager to handle multiple connections
class ConnectionManager:
    def __init__(self):
        self.active_connections: dict = {}

    async def connect(self, websocket: WebSocket, user_id: int):
        await websocket.accept()
        self.active_connections[user_id] = websocket

    def disconnect(self, user_id: int):
        if user_id in self.active_connections:
            del self.active_connections[user_id]

    async def send_personal_message(self, message: str, user_id: int):
        if user_id in self.active_connections:
            await self.active_connections[user_id].send_text(message)

manager = ConnectionManager()

# Save the message to both User1 and User2 collections
async def save_message(user_id: int, message: str):
    await messages_user1.insert_one({"user_id": user_id, "message": message})
    await messages_user2.insert_one({"user_id": user_id, "message": message})

# Get all messages for respective user
async def get_all_messages(user_id: int):
    if user_id == 1:
        messages = await messages_user1.find().to_list(1000)
    elif user_id == 2:
        messages = await messages_user2.find().to_list(1000)
    return messages

# Delete User2's messages (accessible only to User1)
@app.delete("/delete_user2_messages")
async def delete_user2_messages(user_id: int):
    if user_id != 1:
        raise HTTPException(status_code=403, detail="Only User1 can delete User2's messages.")
    
    result = await messages_user2.delete_many({})
    
    # Notify User2 via WebSocket to refresh their page
    if 2 in manager.active_connections:
        await manager.send_personal_message(json.dumps({"action": "refresh"}), 2)
    
    return {"deleted_count": result.deleted_count}

# WebSocket endpoint for both users
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await manager.connect(websocket, user_id)

    # Send all previous messages to the user when they connect
    previous_messages = await get_all_messages(user_id)
    for message in previous_messages:
        await websocket.send_text(json.dumps({"user_id": message["user_id"], "message": message["message"]}))

    try:
        while True:
            data = await websocket.receive_text()
            message_data = json.loads(data)
            await save_message(user_id, message_data['message'])
            target_user_id = 1 if user_id == 2 else 2
            await manager.send_personal_message(json.dumps({"user_id": user_id, "message": message_data['message']}), target_user_id)

    except WebSocketDisconnect:
        manager.disconnect(user_id)
