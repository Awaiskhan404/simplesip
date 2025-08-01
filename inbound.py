from simplesip import SimpleSIPClient
import time
import threading
import pyaudio

client = SimpleSIPClient("1001", "ba5cc9c1a2b8632caf467b326e9e27e6", "10.128.50.210")
audio = None
input_stream = None
output_stream = None
running = False
audio_queue = []
call_established = False

# Audio format (will be dynamically configured by client)
FORMAT = pyaudio.paInt16
CHANNELS = 1

def list_microphones():
    """List available microphones and let user choose"""
    audio = pyaudio.PyAudio()
    
    print("\n🎤 Available Microphones:")
    print("-" * 50)
    
    input_devices = []
    for i in range(audio.get_device_count()):
        info = audio.get_device_info_by_index(i)
        if info['maxInputChannels'] > 0:  # Input device
            input_devices.append((i, info))
            print(f"{len(input_devices)}. {info['name']} (Device {i})")
            print(f"   Channels: {info['maxInputChannels']}, Rate: {info['defaultSampleRate']}")
    
    audio.terminate()
    
    if not input_devices:
        print("❌ No input devices found!")
        return None
    
    print("-" * 50)
    while True:
        try:
            choice = input(f"Choose microphone (1-{len(input_devices)}) or Enter for default: ").strip()
            if not choice:
                return None  # Use default
            
            idx = int(choice) - 1
            if 0 <= idx < len(input_devices):
                device_id, device_info = input_devices[idx]
                print(f"✅ Selected: {device_info['name']}")
                return device_id
            else:
                print(f"❌ Please enter 1-{len(input_devices)}")
        except ValueError:
            print("❌ Please enter a number")
        except KeyboardInterrupt:
            return None

def audio_receive_callback(audio_data, format_type, play_time=None, codec=None):
    """Callback for received audio - client automatically handles codec decoding"""
    global audio_queue
    
    if format_type == 'pcm':
        # Client has already decoded the audio to PCM
        audio_queue.append(audio_data)

def audio_playback_thread():
    """Thread to play received audio"""
    global running, audio_queue
    
    while running:
        if audio_queue and output_stream:
            try:
                pcm_data = audio_queue.pop(0)
                output_stream.write(pcm_data)
            except Exception as e:
                print(f"Playback error: {e}")
        else:
            time.sleep(0.01)  # Short sleep when no audio

def audio_capture_thread():
    """Audio capture - client automatically handles codec encoding"""
    global running
    
    while running and client.running:
        if client.call_state.value in ['connected', 'streaming']:
            try:
                # Get audio config from client
                config = client.get_audio_config()
                chunk_size = config['chunk_size']
                
                # Read audio from microphone
                pcm_data = input_stream.read(chunk_size, exception_on_overflow=False)
                
                # Send to client - it will automatically encode based on negotiated codec
                client.send_audio(pcm_data)
                    
            except Exception as e:
                print(f"Audio capture error: {e}")
                
        time.sleep(0.02)  # 20ms

def setup_audio_streams(mic_device_id=None):
    """Setup audio streams using client's audio configuration"""
    global audio, input_stream, output_stream
    
    # Get audio configuration from client
    config = client.get_audio_config()
    rate = config['sample_rate']
    chunk_size = config['chunk_size']
    
    print(f"🎵 Setting up audio streams:")
    print(f"   Codec: {config['codec']}")
    print(f"   Sample Rate: {rate}Hz")
    print(f"   Chunk Size: {chunk_size} samples")
    
    # Initialize audio
    audio = pyaudio.PyAudio()
    
    # Open input stream (microphone)
    input_kwargs = {
        'format': FORMAT,
        'channels': CHANNELS, 
        'rate': rate,
        'input': True,
        'frames_per_buffer': chunk_size
    }
    
    if mic_device_id is not None:
        input_kwargs['input_device_index'] = mic_device_id
        
    input_stream = audio.open(**input_kwargs)
    
    # Open output stream (speakers)
    output_stream = audio.open(
        format=FORMAT,
        channels=CHANNELS,
        rate=rate,
        output=True,
        frames_per_buffer=chunk_size
    )

def call_state_monitor():
    """Monitor call state changes and setup audio when call connects"""
    global call_established
    last_state = None
    
    while running:
        current_state = client.call_state.value
        
        # Detect state changes
        if current_state != last_state:
            print(f"📞 Call state changed: {last_state} → {current_state}")
            last_state = current_state
            
            # Setup audio when call becomes connected
            if current_state == 'connected' and not call_established:
                print("🎵 Call connected! Setting up audio...")
                
                # Get negotiated codec information
                config = client.get_audio_config()
                print(f"✅ Connected with {config['codec']} codec")
                
                if config['codec'] == 'G722':
                    print("🎉 High-quality wideband audio active!")
                else:
                    print(f"📻 Using {config['codec']} codec")
                
                # Choose microphone if not already done
                if not call_established:
                    mic_device_id = list_microphones()
                    
                    # Set up audio callback for receiving (client handles codec decoding)
                    client.set_audio_callback(audio_receive_callback, 'pcm')
                    
                    # Setup audio streams with correct configuration
                    setup_audio_streams(mic_device_id)
                    
                    print("🎤 Audio started - you can now talk and hear!")
                    print("💡 Client automatically handles codec encoding/decoding")
                    
                    # Start audio threads
                    capture_thread = threading.Thread(target=audio_capture_thread, daemon=True)
                    playback_thread = threading.Thread(target=audio_playback_thread, daemon=True)
                    
                    capture_thread.start()
                    playback_thread.start()
                    
                    call_established = True
            
            # Reset when call ends
            elif current_state == 'idle':
                call_established = False
                print("📴 Call ended")
        
        time.sleep(0.1)
