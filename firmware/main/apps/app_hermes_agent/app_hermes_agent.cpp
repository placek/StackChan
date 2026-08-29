/*
 * SPDX-FileCopyrightText: 2026 M5Stack Technology CO LTD
 *
 * SPDX-License-Identifier: MIT
 */
#include "app_hermes_agent.h"
#include <hal/hal.h>
#include <mooncake.h>
#include <mooncake_log.h>
#include <assets/assets.h>
#include <smooth_lvgl.hpp>
#include <stackchan/stackchan.h>
#include <apps/common/common.h>

using namespace mooncake;
using namespace smooth_ui_toolkit::lvgl_cpp;

AppHermesAgent::AppHermesAgent()
{
    // Configure App name
    setAppInfo().name = "HERMES";
    // Configure App icon
    static auto icon  = assets::get_image("icon_ai_agent.bin");
    setAppInfo().icon = (void*)&icon;
    // Configure App theme color
    static uint32_t theme_color = 0xFF9F43;
    setAppInfo().userData       = (void*)&theme_color;
}

// Called when the App is installed
void AppHermesAgent::onCreate()
{
    mclog::tagInfo(getAppInfo().name, "on create");
}

// Called when the App is opened
void AppHermesAgent::onOpen()
{
    mclog::tagInfo(getAppInfo().name, "on open");

    auto config = GetHAL().getLocalAgentConfig();

    if (config.otaUrl.empty()) {
        // No local agent server configured yet, show a hint instead of starting
        LvglLockGuard lock;

        _panel = std::make_unique<Container>(lv_screen_active());
        _panel->setSize(320, 240);
        _panel->setAlign(LV_ALIGN_CENTER);
        _panel->setBgColor(lv_color_hex(0xFFF3E4));
        _panel->setBorderWidth(0);
        _panel->setRadius(0);
        _panel->removeFlag(LV_OBJ_FLAG_SCROLLABLE);

        _label_title = std::make_unique<Label>(_panel->get());
        _label_title->setText("Local Agent");
        _label_title->setTextFont(&lv_font_montserrat_24);
        _label_title->setTextColor(lv_color_hex(0x8A4B00));
        _label_title->align(LV_ALIGN_TOP_MID, 0, 46);

        _label_msg = std::make_unique<Label>(_panel->get());
        _label_msg->setText(
            "No local agent server is configured.\n\n"
            "Set your Hermes bridge URL in:\n"
            "SETUP > AI.Agent > Local Server");
        _label_msg->setWidth(290);
        _label_msg->setTextAlign(LV_TEXT_ALIGN_CENTER);
        _label_msg->setTextFont(&lv_font_montserrat_16);
        _label_msg->setTextColor(lv_color_hex(0x66491F));
        _label_msg->align(LV_ALIGN_CENTER, 0, 16);

        view::create_home_indicator([&]() { close(); }, 0xFF9F43, 0x6B3E00);
        return;
    }

    // Route the agent runtime to the configured local server and start it.
    // All apps will be uninstalled in next mooncake update
    if (!config.enabled) {
        config.enabled = true;
        GetHAL().setLocalAgentConfig(config);
    }
    GetHAL().requestXiaozhiStart();
}

// Called repeatedly while the App is running
void AppHermesAgent::onRunning()
{
    LvglLockGuard lock;
    view::update_home_indicator();
}

// Called when the App is closed
void AppHermesAgent::onClose()
{
    mclog::tagInfo(getAppInfo().name, "on close");

    LvglLockGuard lock;
    _label_msg.reset();
    _label_title.reset();
    _panel.reset();
    view::destroy_home_indicator();
}
