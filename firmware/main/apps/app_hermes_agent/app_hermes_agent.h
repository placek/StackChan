/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#pragma once
#include <mooncake.h>
#include <smooth_lvgl.hpp>
#include <memory>

/**
 * @brief Local agent App
 *
 * Same functionality as the AI Agent App, but the agent runtime connects to a
 * self-hosted agent server (e.g. a Hermes agent bridge) instead of the default
 * cloud. The server endpoint is configured in SETUP -> AI.Agent -> Local Server.
 */
class AppHermesAgent : public mooncake::AppAbility {
public:
    AppHermesAgent();

    // Override lifecycle callbacks
    void onCreate() override;
    void onOpen() override;
    void onRunning() override;
    void onClose() override;

private:
    std::unique_ptr<smooth_ui_toolkit::lvgl_cpp::Container> _panel;
    std::unique_ptr<smooth_ui_toolkit::lvgl_cpp::Label> _label_title;
    std::unique_ptr<smooth_ui_toolkit::lvgl_cpp::Label> _label_msg;
};
