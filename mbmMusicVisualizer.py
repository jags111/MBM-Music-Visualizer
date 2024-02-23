# MBM's Music Visualizer: The Visualizer
# Visualize a provided audio file.

# TODO: Split image generation, audio processing, and chart generation into separate nodes.

# TODO: Feature: Add filebased input (json) for prompt sequence.
# TODO: Feature: Add ability to specify specific timecodes for prompts.
# TODO: Feature: Add ability to use a hash (?) of a latent (image?) to generate a dummy (random?) audio input.
# TODO: Feature: Add camera effects similar to Scene Weaver.
# TODO: Feature: Add ability to drag in audio files to the loader.

# Imports
import librosa
import torch
import random
import math
import io
import numpy as np
import matplotlib.pyplot as plt
from typing import Union, Optional
from tqdm import tqdm
from scipy.signal import resample
from PIL import Image

import comfy.samplers
from nodes import common_ksampler

from .mbmPrompt import MbmPrompt
from .mbmMVShared import chartData

# Classes
class MusicVisualizer: # TODO: rename
    """
    Visualize a provided audio file.

    Returns a batch tuple of images.
    """
    # Class Constants
    SEED_MODE_FIXED = "fixed"
    SEED_MODE_RANDOM = "random"
    SEED_MODE_INCREASE = "increase"
    SEED_MODE_DECREASE = "decrease"

    LATENT_MODE_STATIC = "static"
    LATENT_MODE_INCREASE = "increase"
    LATENT_MODE_DECREASE = "decrease"
    LATENT_MODE_FLOW = "flow"
    LATENT_MODE_GAUSS = "guassian"
    LATENT_MODE_BOUNCE = "bounce"

    FEAT_APPLY_METHOD_ADD = "add"
    FEAT_APPLY_METHOD_SUBTRACT = "subtract"

    RETURN_TYPES = ("LATENT", "IMAGE")
    RETURN_NAMES = ("LATENTS", "CHARTS")
    FUNCTION = "process"
    CATEGORY = "MBMnodes/Audio"

    # Constructor
    def __init__(self):
        self.__isBouncingUp = True # Used when `bounce` mode is used to track direction

    # ComfyUI Functions
    @classmethod
    def INPUT_TYPES(s):
        return {
            "required": {
                "prompts": ("PROMPT_SEQ", ),
                "latent_mods": ("TENSOR_1D", ),
                "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
                "latent_image": ("LATENT", ),
                "seed_mode": ([MusicVisualizer.SEED_MODE_FIXED, MusicVisualizer.SEED_MODE_RANDOM, MusicVisualizer.SEED_MODE_INCREASE, MusicVisualizer.SEED_MODE_DECREASE], ),
                "latent_mode": ([MusicVisualizer.LATENT_MODE_BOUNCE, MusicVisualizer.LATENT_MODE_FLOW, MusicVisualizer.LATENT_MODE_STATIC, MusicVisualizer.LATENT_MODE_INCREASE, MusicVisualizer.LATENT_MODE_DECREASE, MusicVisualizer.LATENT_MODE_GAUSS], ),
                "image_limit": ("INT", {"default": -1, "min": -1, "max": 0xffffffffffffffff}), # Provide `<= 0` to use whatever audio sampling comes up with
                "latent_mod_limit": ("FLOAT", {"default": 5.0, "min": -1.0, "max": 10000.0}), # The maximum variation that can occur to the latent based on the latent's mean value. Provide `<= 0` to have no limit

                # TODO: Move these into a KSamplerSettings node?
                # Also might be worth adding a KSamplerSettings to KSamplerInputs node that splits it all out to go into the standard KSampler when done here?
                "model": ("MODEL",),
                "steps": ("INT", {"default": 20, "min": 1, "max": 10000}),
                "cfg": ("FLOAT", {"default": 8.0, "min": 0.0, "max": 100.0, "step":0.1, "round": 0.01}),
                "sampler_name": (comfy.samplers.KSampler.SAMPLERS, ),
                "scheduler": (comfy.samplers.KSampler.SCHEDULERS, ),
                "denoise": ("FLOAT", {"default": 1.0, "min": 0.0, "max": 1.0, "step": 0.01}),
            }
        }

    def process(self,
            prompts: list[MbmPrompt],
            latent_mods: torch.Tensor,
            seed: int,
            latent_image: dict[str, torch.Tensor],
            seed_mode: str,
            latent_mode: str,
            image_limit: int,
            latent_mod_limit: float,

            model,
            steps: int,
            cfg: float,
            sampler_name: str,
            scheduler: str,
            denoise: float,
        ):
        ## Setup
        # Set the random library seeds
        random.seed(seed)
        np.random.default_rng(seed)

        # Get the counts
        desiredFrames = len(prompts[0].positive)
        promptCount = len(prompts)

        ## Validation
        # Make sure if bounce mode is used that the latent mod limit is set
        if (latent_mode == MusicVisualizer.LATENT_MODE_BOUNCE) and (latent_mod_limit <= 0):
            raise ValueError("Latent Mod Limit must be set to `>0` when using the `bounce` Latent Mode")

        # Check if prompts are provided
        if len(prompts) == 0:
            raise ValueError("At least one prompt is required.")

        ## Generation
        # Set the initial prompt
        promptPos = MbmPrompt.buildComfyUiPrompt(
            prompts[0].positive,
            pool=prompts[0].positivePool
        )
        promptNeg = MbmPrompt.buildComfyUiPrompt(
            prompts[0].negative,
            pool=prompts[0].negativePool
        )

        # Prepare latent output tensor
        outputTensor: torch.Tensor = None
        latentTensorMeans = np.zeros(desiredFrames)
        latentTensor = latent_image["samples"].clone()
        for i in (pbar := tqdm(range(desiredFrames), desc="Music Visualization")):
            # Calculate the latent tensor
            latentTensor = self._iterateLatentByMode(
                latentTensor,
                latent_mode,
                latent_mod_limit,
                latent_mods[i]
            )

            # Records the latent tensor's mean
            latentTensorMeans[i] = torch.mean(latentTensor).numpy()

            # Set progress bar info
            pbar.set_postfix({
                "mod": f"{latent_mods[i]:.2f}",
                "prompt": f"{torch.mean(promptPos[0][0]):.4f}",
                "latent": f"{latentTensorMeans[i]:.2f}"
            })

            # Generate the image
            imgTensor = common_ksampler(
                    model,
                    seed,
                    steps,
                    cfg,
                    sampler_name,
                    scheduler,
                    promptPos,
                    promptNeg,
                    {"samples": latentTensor}, # ComfyUI, why package it?
                    denoise=denoise
                )[0]["samples"]

            if outputTensor is None:
                outputTensor = imgTensor
            else:
                outputTensor = torch.vstack((
                    outputTensor,
                    imgTensor
                ))

            # Limit if one if supplied
            if (image_limit > 0) and (i >= (image_limit - 1)):
                break

            # Iterate seed as needed
            seed = self._iterateSeedByMode(seed, seed_mode)

            # Iterate the prompts as needed
            if (promptCount > 1) and ((i + 1) < desiredFrames):
                promptPos = MbmPrompt.buildComfyUiPrompt(
                    prompts[i + 1].positive,
                    pool=prompts[i + 1].positivePool
                )
                promptNeg = MbmPrompt.buildComfyUiPrompt(
                    prompts[i + 1].negative,
                    pool=prompts[i + 1].negativePool
                )

        # Render charts
        chartImages = torch.vstack([
            # self._chartGenerationFeats( # TODO: How to do the combined chart?
            #     {
            #         "seed": f"{seed} ({seed_mode})",
            #         "latent mode": latent_mode,
            #         "latent mod limit": f"{latent_mod_limit:.2f}",
            #         "intensity": f"{intensity:.2f}",
            #         "feat mod max": (f"{feat_mod_max:.2f}" if (feat_mod_max is not None) else "none"),
            #         "feat mod min": (f"{feat_mod_min:.2f}" if (feat_mod_min is not None) else "none"),
            #         "feat mod norm": ("yes" if feat_mod_normalize else "no"),
            #         "hop length": hop_length,
            #         "fps target": f"{fps_target:.2f}",
            #         "frames": desiredFrames
            #     },
            #     tempo,
            #     spectroMean,
            #     chromaMean,
            #     featModifiers,
            #     promptSeq.positives
            # ),
            chartData(latentTensorMeans, "Latent Means")
        ])

        # Return outputs
        return (
            {"samples": outputTensor},
            chartImages
        )

    # Internal Functions
    def _iterateLatentByMode(self,
            latent: torch.Tensor,
            latentMode: str,
            modLimit: float,
            modifier: float
        ) -> torch.Tensor:
        """
        Produces a latent tensor based on the provided mode.

        latent: The latent tensor to modify.
        latentMode: The mode to iterate by.
        modLimit: The maximum variation that can occur to the latent based on the latent's mean value. Provide `<= 0` to have no limit.
        modifier: The amount to modify the latent by each hop.

        Returns the iterated latent tensor.
        """
        # Decide what to do if in flow mode
        if latentMode == MusicVisualizer.LATENT_MODE_FLOW:
            # Each hop will add or subtract, based on the audio features, from the last latent
            if random.random() < 0.5:
                latentMode = MusicVisualizer.LATENT_MODE_INCREASE
            else:
                latentMode = MusicVisualizer.LATENT_MODE_DECREASE

        if latentMode == MusicVisualizer.LATENT_MODE_BOUNCE:
            # Increases to to the `modLimit`, then decreases to `-modLimit`, and loops as many times as needed building on the last latent
            if modLimit > 0:
                # Do the bounce operation
                # Calculate the next value
                curLatentMean = torch.mean(latent)
                nextValue = (curLatentMean + modifier) if self.__isBouncingUp else (curLatentMean - modifier)

                # Check if within bounds
                if -modLimit <= nextValue <= modLimit:
                    # Within bounds
                    latentMode = MusicVisualizer.LATENT_MODE_INCREASE if self.__isBouncingUp else MusicVisualizer.LATENT_MODE_DECREASE
                else:
                    # Outside of bounds
                    latentMode = MusicVisualizer.LATENT_MODE_DECREASE if self.__isBouncingUp else MusicVisualizer.LATENT_MODE_INCREASE
                    self.__isBouncingUp = not self.__isBouncingUp
            else:
                # No limit so just increase
                latentMode = MusicVisualizer.LATENT_MODE_INCREASE

        # Decide what to do based on mode
        if latentMode == MusicVisualizer.LATENT_MODE_INCREASE:
            # Each hop adds, based on the audio features, to the last latent
            return self._applyFeatToLatent(latent, MusicVisualizer.FEAT_APPLY_METHOD_ADD, modLimit, modifier)
        elif latentMode == MusicVisualizer.LATENT_MODE_DECREASE:
            # Each hop subtracts, based on the audio features, from the last latent
            return self._applyFeatToLatent(latent, MusicVisualizer.FEAT_APPLY_METHOD_SUBTRACT, modLimit, modifier)
        elif latentMode == MusicVisualizer.LATENT_MODE_GAUSS:
            # Each hop creates a new latent with guassian noise
            return self._createLatent(latent.shape)
        else: # LATENT_MODE_STATIC
            # Only the provided latent is used ignoring audio features
            return latent

    def _createLatent(self, size: tuple) -> torch.Tensor:
        """
        Creates a latent tensor from normal distribution noise.

        size: The size of the latent tensor.

        Returns the latent tensor.
        """
        # TODO: More specific noise range input?
        return torch.tensor(np.random.normal(3, 2.5, size=size))

    def _applyFeatToLatent(self,
            latent: torch.Tensor,
            method: str,
            modLimit: float,
            modifier: float
        ) -> torch.Tensor:
        """
        Applys the provided features to the latent tensor.

        latent: The latent tensor to modify.
        method: The method to use to apply the features.
        modLimit: The maximum variation that can occur to the latent based on the latent's mean value. Provide `<= 0` to have no limit.
        modifier: The amount to modify the latent by each hop.

        Returns the modified latent tensor.
        """
        # Apply features to every point in the latent
        if method == MusicVisualizer.FEAT_APPLY_METHOD_ADD:
            # Add the features to the latent
            # Check if mean will be exceeded
            if (modLimit > 0) and (torch.mean(latent) + modifier) > modLimit:
                # Mean is exceeded so latent only
                return latent

            # Add the features to the latent
            latent += modifier
        else: # FEAT_APPLY_METHOD_SUBTRACT
            # Subtract the features from the latent
            # Check if mean will be exceeded
            if (modLimit > 0) and (torch.mean(latent) - modifier) < -modLimit:
                # Mean is exceeded so latent only
                return latent

            # Subtract the features from the latent
            latent -= modifier

        return latent

    def _iterateSeedByMode(self, seed: int, seedMode: str):
        """
        Produces a seed based on the provided mode.

        seed: The seed to iterate.
        seedMode: The mode to iterate by.

        Returns the iterated seed.
        """
        if seedMode == MusicVisualizer.SEED_MODE_RANDOM:
            # Seed is random every hop
            return random.randint(0, 0xffffffffffffffff)
        elif seedMode == MusicVisualizer.SEED_MODE_INCREASE:
            # Seed increases by 1 every hop
            return seed + 1
        elif seedMode == MusicVisualizer.SEED_MODE_DECREASE:
            # Seed decreases by 1 every hop
            return seed - 1
        else: # SEED_MODE_FIXED
            # Seed stays the same
            return seed

    # def _chartGenerationFeats(self,
    #         renderParams: dict[str, str],
    #         tempo,
    #         spectroMean,
    #         chromaMean,
    #         featModifiers,
    #         promptSeqPos
    #     ) -> torch.Tensor:
    #     """
    #     Creates a chart representing the entire generation flow.

    #     renderParams: The parameters used to render the chart.
    #     tempo: The tempo feature data.
    #     spectroMean: The spectrogram mean feature data.
    #     chromaMean: The chroma mean feature data.
    #     featModifiers: The calculated feature modifiers.
    #     promptSeqPos: The positive prompt sequence.

    #     Returns a ComfyUI compatible Tensor image of the chart.
    #     """
    #     # Build the chart
    #     fig, ax = plt.subplots(figsize=(20, 4))

    #     ax.plot(self._normalizeArray(tempo), label="Tempo")
    #     ax.plot(self._normalizeArray(spectroMean), label="Spectro Mean")
    #     ax.plot(self._normalizeArray(chromaMean), label="Chroma Mean")
    #     ax.plot(self._normalizeArray(featModifiers), label="Modifiers")
    #     ax.plot(self._normalizeArray([torch.mean(c) for c in promptSeqPos]), label="Prompt")

    #     ax.legend()
    #     ax.grid(True)
    #     ax.set_title("Normalized Combined Data")
    #     ax.set_xlabel("Index")
    #     ax.set_ylabel("Value")

    #     # Add the render parameters
    #     renderParams = "\n".join([f"{str(k).strip()}: {str(v).strip()}" for k, v in renderParams.items()])
    #     ax.text(1.02, 0.5, renderParams, transform=ax.transAxes, va="center")

    #     # Render the chart
    #     return self._renderChart(fig)
