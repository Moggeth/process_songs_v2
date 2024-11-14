import subprocess
import os
import json
import time
import pyperclip
from openai import OpenAI
from termcolor import colored
from mutagen.mp3 import MP3

openai_api_key = os.getenv("OPENAI_API_KEY_WORK")
client = OpenAI(api_key=openai_api_key)

# User specified directories
parent_folder = r"C:\Users\mog\Downloads\youtube_songs"

working_folder = os.path.join(parent_folder, "Original Song Files")
output_folder_base = os.path.join(parent_folder, "Output")
verification_folder = os.path.join(output_folder_base, "_verification_needed")

# Create directories if they don't exist
os.makedirs(working_folder, exist_ok=True)
os.makedirs(output_folder_base, exist_ok=True)
os.makedirs(verification_folder, exist_ok=True)

# Keep track of songs that were automatically searched
auto_searched_songs = []
critical_metadata_verification = []
validation_failed_songs = []

# Function to validate the entire song list with GPT in one go
def validate_song_list(song_list):
    song_list_text = '\n'.join([f"{song}" for song in song_list])
    instructions = f'''Here is a list of songs. Please validate if the artist matches the song title. Respond strictly in the following JSON format for each entry:
    [
      {{
        "original_input": "original song input",
        "song_name": "pretty song title",
        "artist_correct": true/false,
        "correct_artist": "correct artist (if applicable)",
        "release_year": "year of release (if applicable)"
      }},
      ...
    ]
    
    Song list:
    {song_list_text}
    '''
    
    retry_attempts = 3
    for attempt in range(retry_attempts):
        response = get_response_from_gpt4(instructions)
        # print(f"Response for {instructions} =  \n\n{response}")
        response = remove_code_block_fences(response)
        
        try:
            validation_results = json.loads(response)
            if isinstance(validation_results, list):
                return validation_results
        except json.JSONDecodeError:
            print(colored(f"[WARNING] Attempt {attempt + 1}: GPT returned invalid JSON. Retrying...", "yellow"))
            print(colored(f"[DEBUG] Raw response from GPT: {response}", "red"))
            time.sleep(2)  # Small delay before retrying
    
    raise ValueError(f"[ERROR] GPT failed to process the song validation after {retry_attempts} attempts.")

# Function to download a song from a given URL or search if URL not provided
def download_one_song(song_name, artist, song_url, output_filename):
    if not song_url:
        # Search for the song on YouTube if URL is not provided
        search_term = f"{artist} - {song_name}"
        print(colored(f"[INFO] Searching YouTube for: {search_term}", "yellow"))
        search_command = [
            "yt-dlp", f"ytsearch:{search_term}", "--get-id"]
        result = subprocess.run(search_command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        song_id = result.stdout.decode().strip()
        if not song_id:
            print(colored(f"[WARNING] Could not find a YouTube result for {song_name}", "red"))
            return
        song_url = f"https://www.youtube.com/watch?v={song_id}"
        auto_searched_songs.append(song_name)
    
    print(colored(f"[INFO] Downloading song: {search_term}", "yellow"))
    command = [
        "yt-dlp", "-x", "--audio-format", "best", song_url, "-o", output_filename
    ]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

# Download a list of songs from YouTube URLs
def download_songs(song_list, output_dir, artist_list):
    print(colored("[INFO] Downloading the following songs:", "cyan"))
    for song_name, song_url in song_list.items():
        print(colored(f" - {song_name}", "cyan"))
        artist = artist_list.get(song_name, "Unknown Artist")
        formatted_song_name = f"{artist} - {song_name}"
        output_filename = f"{output_dir}/{formatted_song_name}.%(ext)s"
        download_one_song(song_name, artist, song_url, output_filename)

# GPT-4 interaction to get response
def get_response_from_gpt4(message, temperature=0.3):
    completion = client.chat.completions.create(
        model="gpt-4o",
        temperature=temperature,
        messages=[
            {"role": "user", "content": message}
        ]
    )
    response_text = completion.choices[0].message.content
    return response_text

# Function to remove markdown code block fences from GPT responses
def remove_code_block_fences(text):
    if text.startswith('```'):
        first_newline = text.find('\n')
        if first_newline != -1:
            text = text[first_newline+1:]
            ending_fence_pos = text.rfind('```')
            if ending_fence_pos != -1:
                text = text[:ending_fence_pos]
    return text.strip()

# Function to process a song (normalize volume, trim, flatten audio to mono, fade out)
def process_song(song_filepath, song_name, output_folder_base, artist, release_year, verification_needed=False):
    output_song_name = process_song_name(song_name, artist)
    if verification_needed:
        output_folder = os.path.join(verification_folder, output_song_name)
    else:
        output_folder = os.path.join(output_folder_base, output_song_name)
    
    os.makedirs(output_folder, exist_ok=True)
    output_song_path = f"{output_folder}/{output_song_name}.mp3"
    metadata_path = f"{output_folder}/metadata.yaml"
    
    print(colored(f"[INFO] Normalizing, trimming, flattening audio to mono, and applying fade-out to song: {artist} - {song_name}", "blue"))
    command = [
        'ffmpeg', '-y', '-i', song_filepath,
        '-af', 'loudnorm=I=-14:LRA=11:TP=-2',  # Volume normalization
        '-t', '180',  # Trim to 180 seconds
        '-ac', '1',  # Flatten to mono
        '-af', 'afade=t=out:st=175:d=5',  # Apply fade-out effect
        '-c:a', 'libmp3lame', '-b:a', '320k',  # Audio codec and bitrate
        output_song_path
    ]
    subprocess.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    
    print(colored(f"[INFO] Processed song: {artist} - {song_name}, saved to: {output_song_path}", "green"))
    
    # Get length of the processed song
    audio = MP3(output_song_path)
    length_in_seconds = int(audio.info.length)

    # Write metadata including length and release year
    with open(metadata_path, "w") as f:
        f.write(f"artist: {artist}\n")
        f.write(f"title: {song_name}\n")
        f.write(f"length: {length_in_seconds}\n")
        f.write(f"year: {release_year}\n")

# Function to sanitize song names
def process_song_name(song_name, artist):
    suffix_text = '''
    Process the following "artist and song title" and return ONLY a filename in the format artist_name_song_name with no file extension. Do not include any additional text.'''
    instructions_to_return_song_filename = f"{artist} - {song_name}" + suffix_text
    processed_song_name = get_response_from_gpt4(instructions_to_return_song_filename)
    processed_song_name = remove_code_block_fences(processed_song_name)
    # Sanitize filename to remove invalid characters
    invalid_chars = r'<>:"/\|?*'
    for char in invalid_chars:
        processed_song_name = processed_song_name.replace(char, "_")
    
    # Add a fallback if the processed song name is not valid
    if not processed_song_name or "artist and song title" in processed_song_name.lower():
        processed_song_name = f"{artist}_{song_name}".replace(" ", "_").lower()
    
    return processed_song_name.strip()

# Function to process a folder of songs
def process_folder_of_songs(songs_folder, song_list, output_folder_base, artist_list, release_years):
    print(colored("[INFO] Starting processing of downloaded songs...", "cyan"))
    os.makedirs(verification_folder, exist_ok=True)  # Ensure verification folder exists
    for song_name in song_list:
        for ext in [".mp3", ".opus", ".m4a", ".flac"]:
            artist = artist_list.get(song_name, "Unknown Artist")
            release_year = release_years.get(song_name, "Unknown Year")
            formatted_song_name = f"{artist} - {song_name}"
            song_filepath = os.path.join(songs_folder, f"{formatted_song_name}{ext}")
            if os.path.exists(song_filepath):
                try:
                    verification_needed = song_name in auto_searched_songs
                    process_song(song_filepath, song_name, output_folder_base, artist, release_year, verification_needed)
                except RuntimeError as e:
                    print(colored(f"[ERROR] Failed to process {song_name}: {e}", "red"))
                break

# Main function to run the script
def main():
    use_clipboard = True
    if use_clipboard:
        print(colored("[INFO] Using clipboard to get song list...", "cyan"))
        clipboard_content = pyperclip.paste()
        song_list = {line.strip(): "" for line in clipboard_content.splitlines() if line.strip()}
    else:
        print(colored("[INFO] Using hardcoded song list...", "green"))
        song_list = {}

    if not song_list:
        print(colored("[ERROR] No songs were provided to process.", "red"))
        return

    # Validate entire song list with GPT
    print(colored("[INFO] Validating song list...", "cyan"))
    try:
        validation_results = validate_song_list(list(song_list.keys()))
    except ValueError as e:
        print(colored(str(e), "red"))
        return

    valid_song_list = {}
    release_years = {}
    for result in validation_results:
        original_input = result['original_input']
        if not result['artist_correct']:
            correct_artist = result.get('correct_artist', 'Unknown')
            print(colored(f"[WARNING] Incorrect artist for '{original_input}'. Correct artist might be: {correct_artist}", "red"))
            validation_failed_songs.append(original_input)
        else:
            pretty_song_name = result['song_name']
            valid_song_list[pretty_song_name] = song_list[original_input]
            release_years[pretty_song_name] = result.get('release_year', 'Unknown')

    if validation_failed_songs:
        print(colored("\n[INFO] The following songs failed validation and will not be processed:", "red"))
        for song in validation_failed_songs:
            print(colored(f" - {song}", "cyan"))

    if not valid_song_list:
        print(colored("[INFO] No valid songs to process after validation.", "red"))
        return

    print(colored("[INFO] Starting download of songs...", "green"))
    artist_list = {result['song_name']: result.get('correct_artist', 'Unknown Artist') for result in validation_results if result['artist_correct']}
    download_songs(valid_song_list, working_folder, artist_list)
    process_folder_of_songs(working_folder, valid_song_list, output_folder_base, artist_list, release_years)
    print(colored("[INFO] All tasks completed.", "magenta"))

if __name__ == "__main__":
    main()
