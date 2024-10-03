from flask import Flask, render_template, request
from googleapiclient.discovery import build
from pytubefix import YouTube
from pytubefix.cli import on_progress
from moviepy.editor import VideoFileClip
import speech_recognition as sr
from pydub import AudioSegment
import cv2
import pytesseract
import os
from datetime import datetime, timedelta
import tempfile

app = Flask(__name__)

YOUTUBE_API_KEY = os.getenv('YOUTUBE_API_KEY')
TESSERACT_CMD = os.getenv('TESSERACT_CMD')

def search_videos(keyword):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    
    # Calculate the date one month ago from today
    one_month_ago = datetime.utcnow() - timedelta(days=30)
    # Format the date in RFC 3339 format
    published_after = one_month_ago.isoformat("T") + "Z"
    
    request = youtube.search().list(
        q=keyword,
        part='id,snippet',
        maxResults=5,  # You can adjust this as needed
        type='video',
        order='viewCount',
        publishedAfter=published_after  # Filter for videos published after this date
    )
    
    response = request.execute()
    
    # Extract video IDs from the response
    video_ids = [item['id']['videoId'] for item in response.get('items', [])]
    
    return video_ids

def get_video_details(video_ids):
    youtube = build('youtube', 'v3', developerKey=YOUTUBE_API_KEY)
    request = youtube.videos().list(
        part='snippet,statistics,contentDetails',
        id=','.join(video_ids)
    )
    response = request.execute()
    videos = []
    for item in response.get('items', []):
        video_data = {
            'video_id': item['id'],
            'title': item['snippet']['title'],
            'views': int(item['statistics'].get('viewCount', 0)),
            'likes': int(item['statistics'].get('likeCount', 0)),
            'comments': int(item['statistics'].get('commentCount', 0)),
            'thumbnail': item['snippet']['thumbnails']['high']['url'],
            'duration': item['contentDetails']['duration'],
            'description': item['snippet']['description'],
            'channel_title': item['snippet']['channelTitle'],
        }
        videos.append(video_data)
    return videos

def download_and_process_video(video_id):
    # Define file paths
    original_video = f'{video_id}.mp4'
    trimmed_video = f'{video_id}_trimmed.mp4'
    audio_file = f'{video_id}.wav'
    frames = [f"frame_{video_id}_{count}.jpg" for count in range(5)]
    
    try:
        # Download video
        url = f'https://www.youtube.com/watch?v={video_id}'
        print(f"Downloading video from: {url}")
        yt = YouTube(url, on_progress_callback=on_progress)
        stream = yt.streams.filter(file_extension='mp4').first()
        if not stream:
            print(f"No MP4 video stream found for {video_id}")
            return 'No MP4 video stream available'
    
        print(f"Downloading video stream: {stream}")
        stream.download(filename=original_video)
    
        # Use context manager for VideoFileClip to ensure the file is closed after use
        print(f"Trimming video to first 3 seconds...")
        with VideoFileClip(original_video) as clip:
            # Trim to first 3 seconds and save the result
            clip.subclip(0, 3).write_videofile(trimmed_video, codec='libx264', audio_codec='aac')
            
            # Extract audio from the video
            print(f"Extracting audio from {trimmed_video}...")
            clip.audio.write_audiofile(audio_file)
    
        # Speech-to-text
        recognizer = sr.Recognizer()
        audio = sr.AudioFile(audio_file)
        print(f"Attempting speech recognition from {audio_file}...")
        with audio as source:
            audio_data = recognizer.record(source)
        try:
            speech_text = recognizer.recognize_google(audio_data)
            print(f"Recognized speech: {speech_text}")
        except sr.UnknownValueError:
            speech_text = ''
            print(f"Speech recognition failed: UnknownValueError")
    
        # Extract frames for OCR
        print(f"Extracting frames for OCR (max 5 frames)...")
        vidcap = cv2.VideoCapture(trimmed_video)
        success, image = vidcap.read()
        count = 0
        ocr_text = ''
        while success and count < 5:
            frame_filename = frames[count]
            cv2.imwrite(frame_filename, image)
            img = cv2.imread(frame_filename)
            extracted_text = pytesseract.image_to_string(img)
            ocr_text += extracted_text
            print(f"Extracted OCR text from frame {count}: {extracted_text}")
            os.remove(frame_filename)
            success, image = vidcap.read()
            count += 1
        vidcap.release()
    
        # Combine speech and OCR text
        hook_text = (speech_text + ' ' + ocr_text).strip()
        return hook_text
    
    except Exception as e:
        print(f"An error occurred while processing video {video_id}: {e}")
        return 'Error extracting hook'
    
    finally:
        # Clean up temporary files
        print(f"Cleaning up temporary files for video {video_id}...")
        files_to_delete = [original_video, trimmed_video, audio_file] + frames
        for file in files_to_delete:
            if os.path.exists(file):
                try:
                    os.remove(file)
                    print(f"Deleted file: {file}")
                except Exception as delete_error:
                    print(f"Failed to delete file {file}: {delete_error}")


@app.route('/', methods=['GET', 'POST'])
def index():
    videos = []
    if request.method == 'POST':
        keyword = request.form['keyword']
        video_ids = search_videos(keyword)
        videos = get_video_details(video_ids)
        for video in videos:
            video_id = video['video_id']
            try:
                hook_text = download_and_process_video(video_id)
                video['hook'] = hook_text
            except Exception as e:
                print(f"Error processing video {video_id}: {e}")
                video['hook'] = 'Error extracting hook'
    return render_template('index.html', videos=videos)

if __name__ == '__main__':
    app.run(debug=True)
