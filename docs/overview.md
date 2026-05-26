# Project Documentation Overview

This file provides a categorized overview of the documentation within the `infra_tools_ww_ik` repository.

## 🏗️ Architecture & Design
Documents describing the high-level structure, design patterns, and architectural decisions of the project.

* **`ARCHITECTURE_V2.md`**: Detailed overview of the "v2" architecture, including the game manipulation layer, scanning workflows, and the Qt UI layer.
* **`PIPELINE_MAINTAINABILITY.md`**: Discusses the design and implementation of the processing pipeline with a focus on long-term maintainability.
* **`WINDOWED_MODE_FEASIBILITY.md`**: A feasibility analysis regarding the challenges of supporting windowed mode, specifically focusing on coordinate and screenshot capture assumptions.

## 🔍 OCR & Image Processing
Documents focused on the OCR engine, preprocessing strategies, and image enhancement techniques.

* **`OCR_PREPROCESSING_PLAN.md`**: Outlines the plan to co-locate preprocessing logic and cache-signature parameters alongside ROI definitions.
* **`OCR_PRESERVING_FEATURES.md`**: Explores feature-preverving contrast enhancement techniques to maximize text signal while preserving structural gradients.
* **`RAPIDOCR_COLOR_PREPROCESSING_MIGRATION.md`**: Proposes a migration strategy to leverage RapidOCR's strength in processing 3-channel color input.

## 🛠️ Development & Maintenance
Documents related to the ongoing development, refactoring, and maintenance of the codebase.

* **`REFACTORING_PLAN.md`**: Details the plan to decouple the echo scraper into a two-phase architecture (Scanner and Processor).
* **`PIPELINE_MAINTAINABILITY_STATUS.md`**: Provides the current status of the active V2 pipeline's maintainability.
* **`TODO.md`**: A tracking list for pending tasks, feature requests, and known bugs.
* **`validation.md`**: Describes the structure and constraints of the data validation process for OCR outputs.

## 🌐 Data & Localization
Documents concerning the management of localized data and the data structures used by the application.

* **`LocalizationDataPlan.md`**: Describes the directory layout and strategy for managing multi-language data (achievements, characters, items, etc.).

## 📖 General Information
Core project documentation.

* **`UPSTREAM.md`**: Providing project purpose, supported resolutions, and key features of the project this one was forked from.
