import discord
from discord import app_commands
import firebase_admin
from firebase_admin import credentials, firestore
import os
from datetime import datetime
from flask import Flask, jsonify, request
from flask_cors import CORS
import threading
import uuid
import requests

cred = credentials.Certificate({
    "type": "service_account",
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace('\\n', '\n'),
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
})

firebase_admin.initialize_app(cred)
db = firestore.client()

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)
tree = app_commands.CommandTree(client)

app = Flask(__name__)
CORS(app, origins=["*"])

DISCORD_CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
DISCORD_CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
DISCORD_REDIRECT_URI = os.getenv("DISCORD_REDIRECT_URI")
DISCORD_GUILD_ID = os.getenv("DISCORD_GUILD_ID")
DISCORD_ADMIN_ROLE_ID = os.getenv("DISCORD_ADMIN_ROLE_ID")

@tree.command(name="register", description="Register with DHL Flight Operations")
async def register(interaction: discord.Interaction):
    user_ref = db.collection('users').document(str(interaction.user.id))
    user_doc = user_ref.get()
    
    if user_doc.exists:
        await interaction.response.send_message("You are already registered.", ephemeral=True)
        return
    
    user_ref.set({
        'discordId': str(interaction.user.id),
        'username': interaction.user.name,
        'loyaltyPoints': 0,
        'packagesSubmitted': 0,
        'packagesDelivered': 0,
        'registeredAt': datetime.utcnow().isoformat()
    })
    
    await interaction.response.send_message("Successfully registered with DHL Flight Operations.", ephemeral=True)

@tree.command(name="submit_package", description="Submit a package for delivery")
async def submit_package(interaction: discord.Interaction, destination: str):
    user_ref = db.collection('users').document(str(interaction.user.id))
    user_doc = user_ref.get()
    
    if not user_doc.exists:
        await interaction.response.send_message("Please register first using /register", ephemeral=True)
        return
    
    package_id = str(uuid.uuid4())
    package_ref = db.collection('packages').document(package_id)
    package_ref.set({
        'id': package_id,
        'userId': str(interaction.user.id),
        'destination': destination,
        'status': 'pending',
        'submittedAt': datetime.utcnow().isoformat()
    })
    
    user_data = user_doc.to_dict()
    user_ref.update({
        'packagesSubmitted': user_data.get('packagesSubmitted', 0) + 1
    })
    
    await interaction.response.send_message(f"Package submitted to {destination}. ID: {package_id}", ephemeral=True)

@tree.command(name="assign_flight", description="Assign a package to a flight")
@app_commands.checks.has_permissions(administrator=True)
async def assign_flight(interaction: discord.Interaction, package_id: str, flight_number: str):
    package_ref = db.collection('packages').document(package_id)
    package_doc = package_ref.get()
    
    if not package_doc.exists:
        await interaction.response.send_message("Package not found.", ephemeral=True)
        return
    
    package_ref.update({
        'flightNumber': flight_number,
        'status': 'in-transit'
    })
    
    await interaction.response.send_message(f"Package {package_id} assigned to flight {flight_number}", ephemeral=True)

@tree.command(name="complete_flight", description="Mark a flight as complete")
@app_commands.checks.has_permissions(administrator=True)
async def complete_flight(interaction: discord.Interaction, flight_number: str):
    packages = db.collection('packages').where('flightNumber', '==', flight_number).where('status', '==', 'in-transit').stream()
    
    count = 0
    for package in packages:
        package_data = package.to_dict()
        package_ref = db.collection('packages').document(package.id)
        package_ref.update({
            'status': 'delivered',
            'deliveredAt': datetime.utcnow().isoformat()
        })
        
        user_ref = db.collection('users').document(package_data['userId'])
        user_doc = user_ref.get()
        user_data = user_doc.to_dict()
        
        user_ref.update({
            'packagesDelivered': user_data.get('packagesDelivered', 0) + 1,
            'loyaltyPoints': user_data.get('loyaltyPoints', 0) + 100
        })
        
        count += 1
    
    await interaction.response.send_message(f"Flight {flight_number} completed. {count} packages delivered.", ephemeral=True)

@tree.command(name="check_points", description="Check your loyalty points")
async def check_points(interaction: discord.Interaction):
    user_ref = db.collection('users').document(str(interaction.user.id))
    user_doc = user_ref.get()
    
    if not user_doc.exists:
        await interaction.response.send_message("Please register first using /register", ephemeral=True)
        return
    
    user_data = user_doc.to_dict()
    points = user_data.get('loyaltyPoints', 0)
    
    await interaction.response.send_message(f"You have {points} loyalty points.", ephemeral=True)

@tree.command(name="track_package", description="Track a package")
async def track_package(interaction: discord.Interaction, package_id: str):
    package_ref = db.collection('packages').document(package_id)
    package_doc = package_ref.get()
    
    if not package_doc.exists:
        await interaction.response.send_message("Package not found.", ephemeral=True)
        return
    
    package_data = package_doc.to_dict()
    status = package_data.get('status', 'unknown')
    destination = package_data.get('destination', 'unknown')
    flight = package_data.get('flightNumber', 'Not assigned')
    
    await interaction.response.send_message(f"Package {package_id}\nDestination: {destination}\nStatus: {status}\nFlight: {flight}", ephemeral=True)

@app.route('/api/auth/callback', methods=['GET'])
def auth_callback():
    code = request.args.get('code')
    
    if not code:
        return jsonify({'error': 'No code provided'}), 400
    
    token_response = requests.post('https://discord.com/api/oauth2/token', data={
        'client_id': DISCORD_CLIENT_ID,
        'client_secret': DISCORD_CLIENT_SECRET,
        'grant_type': 'authorization_code',
        'code': code,
        'redirect_uri': DISCORD_REDIRECT_URI,
    })
    
    if token_response.status_code != 200:
        return jsonify({'error': 'Failed to get token'}), 400
    
    token_data = token_response.json()
    access_token = token_data['access_token']
    
    user_response = requests.get('https://discord.com/api/users/@me', headers={
        'Authorization': f'Bearer {access_token}'
    })
    
    if user_response.status_code != 200:
        return jsonify({'error': 'Failed to get user'}), 400
    
    user_data = user_response.json()
    user_id = user_data['id']
    username = user_data['username']
    
    member_response = requests.get(
        f'https://discord.com/api/users/@me/guilds/{DISCORD_GUILD_ID}/member',
        headers={'Authorization': f'Bearer {access_token}'}
    )
    
    is_admin = False
    if member_response.status_code == 200:
        member_data = member_response.json()
        roles = member_data.get('roles', [])
        is_admin = DISCORD_ADMIN_ROLE_ID in roles
    
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    
    if not user_doc.exists:
        user_ref.set({
            'discordId': user_id,
            'username': username,
            'loyaltyPoints': 0,
            'packagesSubmitted': 0,
            'packagesDelivered': 0,
            'registeredAt': datetime.utcnow().isoformat()
        })
    
    session_token = str(uuid.uuid4())
    session_ref = db.collection('sessions').document(session_token)
    session_ref.set({
        'userId': user_id,
        'isAdmin': is_admin,
        'createdAt': datetime.utcnow().isoformat()
    })
    
    return jsonify({
        'sessionToken': session_token,
        'isAdmin': is_admin
    })

@app.route('/api/user', methods=['GET'])
def get_user():
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Unauthorized'}), 401
    
    session_token = auth_header.replace('Bearer ', '')
    user_id = verify_session(session_token)
    
    if not user_id:
        return jsonify({'error': 'Unauthorized'}), 401
    
    user_ref = db.collection('users').document(user_id)
    user_doc = user_ref.get()
    
    if not user_doc.exists:
        return jsonify({'error': 'User not found'}), 404
    
    user_data = user_doc.to_dict()
    
    packages = []
    packages_query = db.collection('packages').where('userId', '==', user_id).stream()
    for package in packages_query:
        packages.append(package.to_dict())
    
    return jsonify({
        'user': user_data,
        'packages': packages
    })

@app.route('/api/admin/flights', methods=['GET'])
def get_flights():
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Unauthorized'}), 401
    
    session_token = auth_header.replace('Bearer ', '')
    session_data = verify_session_with_admin(session_token)
    
    if not session_data or not session_data.get('isAdmin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    flights = []
    flights_query = db.collection('flights').stream()
    for flight in flights_query:
        flights.append(flight.to_dict())
    
    return jsonify({'flights': flights})

@app.route('/api/admin/flights/<flight_id>/complete', methods=['POST'])
def complete_flight_api(flight_id):
    auth_header = request.headers.get('Authorization')
    
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Unauthorized'}), 401
    
    session_token = auth_header.replace('Bearer ', '')
    session_data = verify_session_with_admin(session_token)
    
    if not session_data or not session_data.get('isAdmin'):
        return jsonify({'error': 'Unauthorized'}), 401
    
    flight_ref = db.collection('flights').document(flight_id)
    flight_doc = flight_ref.get()
    
    if not flight_doc.exists:
        return jsonify({'error': 'Flight not found'}), 404
    
    flight_data = flight_doc.to_dict()
    flight_number = flight_data.get('flightNumber')
    
    packages = db.collection('packages').where('flightNumber', '==', flight_number).where('status', '==', 'in-transit').stream()
    
    for package in packages:
        package_data = package.to_dict()
        package_ref = db.collection('packages').document(package.id)
        package_ref.update({
            'status': 'delivered',
            'deliveredAt': datetime.utcnow().isoformat()
        })
        
        user_ref = db.collection('users').document(package_data['userId'])
        user_doc = user_ref.get()
        user_data = user_doc.to_dict()
        
        user_ref.update({
            'packagesDelivered': user_data.get('packagesDelivered', 0) + 1,
            'loyaltyPoints': user_data.get('loyaltyPoints', 0) + 100
        })
    
    flight_ref.update({'status': 'completed'})
    
    return jsonify({'success': True})

def verify_session(session_token):
    if not session_token:
        return None
    
    session_ref = db.collection('sessions').document(session_token)
    session_doc = session_ref.get()
    
    if not session_doc.exists:
        return None
    
    session_data = session_doc.to_dict()
    return session_data.get('userId')

def verify_session_with_admin(session_token):
    if not session_token:
        return None
    
    session_ref = db.collection('sessions').document(session_token)
    session_doc = session_ref.get()
    
    if not session_doc.exists:
        return None
    
    return session_doc.to_dict()

def run_flask():
    app.run(host='0.0.0.0', port=5000)

@client.event
async def on_ready():
    await tree.sync()
    print(f'Logged in as {client.user}')

def main():
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.daemon = True
    flask_thread.start()
    
    client.run(os.getenv('DISCORD_BOT_TOKEN'))

if __name__ == '__main__':
    main()
