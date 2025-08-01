from simplesip.client import SimpleSIPClient, CallState
import time
import threading
import pyaudio
import collections

client = SimpleSIPClient("1001", "ba5cc9c1a2b8632caf467b326e9e27e6", "10.128.50.210")
audio = None
input_stream = None
output_stream = None
running = False
audio_queue = collections.deque(maxlen=50)  # Buffer for audio packets

# Audio format
FORMAT = pyaudio.paInt16
CHANNELS = 1
SAMPLE_RATE = 8000  # Start with default, will be updated
CHUNK_SIZE = 160    # Start with default, will be updated

def audio_receive_callback(audio_data, format_type, play_time=None):
    """Fixed callback for received audio"""
    global audio_queue
    
    print(f"🎵 AUDIO RECEIVED: {len(audio_data)} bytes, format: {format_type}")
    
    if format_type == 'pcm' and audio_data:
        # Add to playback queue
        audio_queue.append(audio_data)
        print(f"📋 Audio queue size: {len(audio_queue)}")

def audio_playback_thread():
    """Thread to play received audio"""
    global running, audio_queue
    
    print("🔊 Playback thread started")
    
    while running:
        if audio_queue and output_stream:
            try:
                pcm_data = audio_queue.popleft()
                output_stream.write(pcm_data)
                print(f"▶️  Played {len(pcm_data)} bytes")
            except Exception as e:
                print(f"❌ Playback error: {e}")
        else:
            time.sleep(0.01)
    
    print("🔊 Playback thread stopped")

def audio_capture_thread():
    """Audio capture thread"""
    global running, client, input_stream
    
    print("🎤 Capture thread started")
    
    while running and client.running:
        # Use CallState enum properly
        if client.call_state in [CallState.CONNECTED, CallState.STREAMING]:
            try:
                # Read audio from microphone
                pcm_data = input_stream.read(CHUNK_SIZE, exception_on_overflow=False)
                
                # Send to client
                client.send_audio(pcm_data)
                print(f"📤 Sent {len(pcm_data)} bytes")
                    
            except Exception as e:
                print(f"❌ Capture error: {e}")
                
        time.sleep(0.02)  # 20ms
    
    print("🎤 Capture thread stopped")

def setup_audio_streams():
    """Setup audio streams"""
    global audio, input_stream, output_stream, SAMPLE_RATE, CHUNK_SIZE
    
    # Get audio configuration from client after connection
    config = client.get_audio_config()
    SAMPLE_RATE = config['sample_rate']
    CHUNK_SIZE = config['chunk_size']
    
    print(f"🎵 Setting up audio streams:")
    print(f"   Codec: {config['codec']}")
    print(f"   Sample Rate: {SAMPLE_RATE}Hz")
    print(f"   Chunk Size: {CHUNK_SIZE} samples")
    
    # Initialize audio
    audio = pyaudio.PyAudio()
    
    # Open input stream (microphone)
    input_stream = audio.open(
        format=FORMAT,
        channels=CHANNELS, 
        rate=SAMPLE_RATE,
        input=True,
        frames_per_buffer=CHUNK_SIZE
    )
    
    # Open output stream (speakers)
    output_stream = audio.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=SAMPLE_RATE,
        output=True,
        frames_per_buffer=CHUNK_SIZE
    )
    
    print("✅ Audio streams ready")

def wait_for_call():
    """Wait for call to be established - runs indefinitely"""
    print("⏳ Waiting for call...")
    
    while client.running:
        if client.call_state in [CallState.CONNECTED, CallState.STREAMING]:
            print("📞 Call detected!")
            return True
        elif client.call_state == CallState.RINGING:
            print("🔔 Incoming call ringing...")
        elif client.call_state == CallState.INVITING:
            print("📞 Outgoing call in progress...")
        
        time.sleep(0.5)  # Check every 500ms
    
    return False

try:
    print("🔊 SimpleSIP Audio Client - Fixed Version")
    print("Connecting...")
    
    # Set up audio callback BEFORE connecting
    client.set_audio_callback(audio_receive_callback, format='pcm')
    
    # Connect and register
    client.connect()
    time.sleep(2)
    
    print("📞 Waiting for incoming calls...")
    print("💡 The client will automatically answer incoming calls")
    print("💡 Press Ctrl+C to exit")
    
    # Continuously wait for calls
    while client.running:
        if wait_for_call():
            print("✅ Call connected!")
            
            # Setup audio after call is connected
            setup_audio_streams()
            
            # Start audio threads
            running = True
            capture_thread = threading.Thread(target=audio_capture_thread, daemon=True)
            playback_thread = threading.Thread(target=audio_playback_thread, daemon=True)
            
            capture_thread.start()
            playback_thread.start()
            
            print("🎤 Audio started - you can now talk and hear!")
            
            # Keep running until call ends
            while client.running and client.call_state != CallState.IDLE:
                time.sleep(1)
                if len(audio_queue) > 0:
                    print(f"📊 Call active, Audio queue: {len(audio_queue)}")
            
            # Call ended, cleanup
            running = False
            time.sleep(0.5)  # Let threads finish
            
            if input_stream:
                input_stream.close()
                input_stream = None
            if output_stream:
                output_stream.close()
                output_stream = None
            if audio:
                audio.terminate()
                audio = None
                
            print("📴 Call ended, waiting for next call...")
        else:
            # Client stopped running
            break
            
except KeyboardInterrupt:
    print("\n🛑 Stopping...")
finally:
    running = False
    time.sleep(0.5)  # Let threads finish
    
    if input_stream:
        input_stream.close()
    if output_stream:
        output_stream.close()
    if audio:
        audio.terminate()
    
    client.disconnect()
    print("👋 Goodbye!")