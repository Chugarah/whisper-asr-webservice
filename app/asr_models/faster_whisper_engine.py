import time
import os
from io import StringIO
from threading import Thread
from typing import BinaryIO, Union

import whisper
from faster_whisper import WhisperModel

from app.asr_models.asr_model import ASRModel
from app.config import CONFIG
from app.utils import ResultWriter, WriteJSON, WriteSRT, WriteTSV, WriteTXT, WriteVTT, WriteAll


class FasterWhisperASR(ASRModel):

    def load_model(self):

        self.model = WhisperModel(
            model_size_or_path=CONFIG.MODEL_NAME,
            device=CONFIG.DEVICE,
            compute_type=CONFIG.MODEL_QUANTIZATION,
            download_root=CONFIG.MODEL_PATH
        )

        Thread(target=self.monitor_idleness, daemon=True).start()

    def transcribe(
            self,
            audio,
            task: Union[str, None],
            language: Union[str, None],
            initial_prompt: Union[str, None],
            vad_filter: Union[bool, None],
            word_timestamps: Union[bool, None],
            options: Union[dict, None],
            output,
    ):
        self.last_activity_time = time.time()

        with self.model_lock:
            if self.model is None:
                self.load_model()

        options_dict = {"task": task}
        if language:
            options_dict["language"] = language
        if initial_prompt:
            options_dict["initial_prompt"] = initial_prompt
        if vad_filter:
            options_dict["vad_filter"] = True
        if word_timestamps:
            options_dict["word_timestamps"] = True
        with self.model_lock:
            segments = []
            text = ""
            segment_generator, info = self.model.transcribe(audio, beam_size=5, **options_dict)
            for segment in segment_generator:
                segments.append(segment)
                text = text + segment.text
            result = {"language": options_dict.get("language", info.language), "segments": segments, "text": text}

        # Store the output directory and audio path for the "all" option
        self.output_dir = os.environ.get("OUTPUT_DIR", "/tmp")
        self.audio_path = os.environ.get("AUDIO_FILENAME", "audio")

        # For "all" output format, create and return the zip bytes
        if output == "all":
            writer = WriteAll(self.output_dir)
            zip_bytes = writer.create_zip_bytes(result)
            # Create a generator that yields the bytes
            def bytes_generator():
                yield zip_bytes
            return bytes_generator()
            
        # For other formats, write to StringIO and return that
        output_file = StringIO()
        self.write_result(result, output_file, output)
        output_file.seek(0)
        return output_file

    def language_detection(self, audio):

        self.last_activity_time = time.time()

        with self.model_lock:
            if self.model is None: self.load_model()

        # load audio and pad/trim it to fit 30 seconds
        audio = whisper.pad_or_trim(audio)

        # detect the spoken language
        with self.model_lock:
            segments, info = self.model.transcribe(audio, beam_size=5)
            detected_lang_code = info.language
            detected_language_confidence = info.language_probability

        return detected_lang_code, detected_language_confidence

    def write_result(self, result: dict, file: BinaryIO, output: Union[str, None]):
        """
        Write the transcription result to the specified output format.
        
        For 'all' format, this function is not directly used as the transcribe method
        handles it with create_zip_bytes.
        For other formats, writes directly to the provided file object.
        """
        # Initialize the appropriate writer class based on the output format
        if output == "srt":
            writer_class = WriteSRT
        elif output == "vtt":
            writer_class = WriteVTT
        elif output == "tsv":
            writer_class = WriteTSV
        elif output == "json":
            writer_class = WriteJSON
        else:  # Default to txt
            writer_class = WriteTXT
        
        # Create a ResultWriter instance and write to the file
        writer = writer_class(self.output_dir)
        writer.write_result(result, file=file)
