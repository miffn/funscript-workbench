// HUD extracted from miffn/OFS-custom@d341a387; see vendor/PROVENANCE.md.
#pragma once
#include <imgui.h>
#include <array>
#include <cstdio>
#include <cstdarg>
#include <algorithm>
#include <cmath>
namespace Util {
template<typename T> T Clamp(T x,T lo,T hi) { return std::clamp(x,lo,hi); }
inline const char* Format(const char* format,...) {
    static thread_local char buffers[8][128]; static thread_local unsigned index=0;
    char* result=buffers[index++%8]; va_list args; va_start(args,format);
    vsnprintf(result,128,format,args); va_end(args); return result;
}
}
inline float Mix(float a,float b,float t) {return a+(b-a)*t;}
inline int AxisPercent(float v) {return static_cast<int>(std::round(std::clamp(v,0.f,1.f)*100.f));}
struct AxisHud {
enum {AxisStroke,AxisSurge,AxisSway,AxisTwist,AxisRoll,AxisPitch};
struct {bool showLabels=true,showAxisHud=true,axisValueBackground=true; float axisValueTextScale=1.1f;} state;
std::array<bool,6> lastAxisPresent{};
std::array<float,6> lastAxisValues{};
float lastStrokeTravelPercent=0;
bool lastStrokeLineValid=false,lastStrokeBaseScreenBoundsValid=false;
ImVec2 lastStrokeAnchorTop,lastStrokeAnchorBottom,lastStrokeBaseScreenMin,lastStrokeBaseScreenMax;
void draw(ImDrawList* drawList, const ImVec2& min, const ImVec2& max) noexcept
{
    if(!drawList || !state.showLabels || !state.showAxisHud) return;

    const float scale = Util::Clamp<float>(state.axisValueTextScale, 0.7f, 1.8f);
    const float fontSize = ImGui::GetFontSize() * scale;
    const float pad = std::max(6.f, 8.f * scale);
    const float bar = std::max(5.f, 7.f * scale);
    const float lane = std::max(10.f, 14.f * scale);
    const float centerX = (min.x + max.x) * 0.5f;
    const float centerY = (min.y + max.y) * 0.5f;
    const float bottomY = max.y - pad - lane;
    const float topY = min.y + pad;
    const ImU32 bg = IM_COL32(0, 0, 0, state.axisValueBackground ? 135 : 0);
    const ImU32 slot = IM_COL32(255, 255, 255, 42);
    const ImVec4 rollBase(1.00f, 0.39f, 0.19f, 0.90f);
    const ImVec4 pitchBase(0.27f, 0.58f, 1.00f, 0.90f);
    const ImVec4 swayBase(0.16f, 0.88f, 0.45f, 0.90f);
    const ImVec4 surgeBase(0.05f, 0.85f, 0.95f, 0.90f);
    const ImVec4 strokeBase(0.96f, 0.96f, 0.96f, 0.90f);

    auto dangerColor = [](float value, ImVec4 base) -> ImU32 {
        const float t = Util::Clamp<float>(value, 0.f, 1.f);
        float danger = 0.f;
        if(t < 0.2f) danger = (0.2f - t) / 0.2f;
        else if(t > 0.8f) danger = (t - 0.8f) / 0.2f;
        danger = Util::Clamp<float>(danger, 0.f, 1.f);
        const ImVec4 deep(base.x * 0.42f, base.y * 0.42f, base.z * 0.42f, 0.98f);
        ImVec4 out(Mix(base.x, deep.x, danger), Mix(base.y, deep.y, danger), Mix(base.z, deep.z, danger), Mix(base.w, deep.w, danger));
        return ImGui::ColorConvertFloat4ToU32(out);
    };

    auto drawTextCentered = [&](const ImVec2& p, const char* text, ImU32 color) {
        auto textSize = ImGui::CalcTextSize(text);
        textSize.x *= scale;
        textSize.y *= scale;
        const ImVec2 textMin(p.x - textSize.x * 0.5f, p.y - textSize.y * 0.5f);
        if(state.axisValueBackground)
        {
            drawList->AddRectFilled(ImVec2(textMin.x - pad * 0.6f, textMin.y - pad * 0.25f), ImVec2(textMin.x + textSize.x + pad * 0.6f, textMin.y + textSize.y + pad * 0.25f), bg, 4.f);
        }
        drawList->AddText(ImGui::GetFont(), fontSize, textMin, color, text);
    };

    auto drawStrokeBadge = [&](const ImVec2& p, const char* text, float value) {
        const float badgeFontSize = fontSize * 1.22f;
        auto textSize = ImGui::CalcTextSize(text);
        textSize.x *= (badgeFontSize / ImGui::GetFontSize());
        textSize.y *= (badgeFontSize / ImGui::GetFontSize());
        const float dangerLow = value < 0.2f ? (0.2f - value) / 0.2f : 0.f;
        const float dangerHigh = value > 0.8f ? (value - 0.8f) / 0.2f : 0.f;
        const float danger = Util::Clamp<float>(std::max(dangerLow, dangerHigh), 0.f, 1.f);
        const ImVec2 textMin(p.x - textSize.x * 0.5f, p.y - textSize.y * 0.5f);
        const ImVec2 bgMin(textMin.x - pad * 0.9f, textMin.y - pad * 0.45f);
        const ImVec2 bgMax(textMin.x + textSize.x + pad * 0.9f, textMin.y + textSize.y + pad * 0.45f);
        const ImU32 fill = IM_COL32(static_cast<int>(10 + 80 * danger), 10, 10, 230);
        const ImU32 border = danger > 0.001f ? IM_COL32(255, 65, 45, 245) : IM_COL32(245, 245, 245, 210);
        drawList->AddRectFilled(bgMin, bgMax, fill, 6.f);
        drawList->AddRect(bgMin, bgMax, border, 6.f, 0, 1.6f);
        drawList->AddText(ImGui::GetFont(), badgeFontSize, ImVec2(textMin.x + 1.f, textMin.y + 1.f), IM_COL32(0, 0, 0, 180), text);
        drawList->AddText(ImGui::GetFont(), badgeFontSize, textMin, IM_COL32(255, 255, 255, 255), text);
    };

    auto drawHorizontalAxis = [&](float y, float x0, float x1, float value, ImU32 color, bool label, const char* name, bool invert = false) {
        const float cx = (x0 + x1) * 0.5f;
        drawList->AddRectFilled(ImVec2(x0, y), ImVec2(x1, y + bar), slot, bar * 0.5f);
        const float t = invert ? 1.f - Util::Clamp<float>(value, 0.f, 1.f) : Util::Clamp<float>(value, 0.f, 1.f);
        if(t < 0.5f)
        {
            const float end = Mix(x0, cx, t * 2.f);
            drawList->AddRectFilled(ImVec2(end, y), ImVec2(cx, y + bar), color, bar * 0.5f);
        }
        else
        {
            const float end = Mix(cx, x1, (t - 0.5f) * 2.f);
            drawList->AddRectFilled(ImVec2(cx, y), ImVec2(end, y + bar), color, bar * 0.5f);
        }
        drawList->AddLine(ImVec2(cx, y - 2.f), ImVec2(cx, y + bar + 2.f), IM_COL32(255, 255, 255, 130), 1.f);
        if(label)
        {
            auto txt = Util::Format("%s %d", name, AxisPercent(value));
            drawTextCentered(ImVec2(cx, y + bar + fontSize * 0.9f), txt, color);
        }
    };

    auto drawVerticalAxis = [&](float x, float y0, float y1, float value, ImU32 color) {
        const float cy = (y0 + y1) * 0.5f;
        drawList->AddRectFilled(ImVec2(x, y0), ImVec2(x + bar, y1), slot, bar * 0.5f);
        const float t = Util::Clamp<float>(value, 0.f, 1.f);
        if(t < 0.5f)
        {
            const float end = Mix(y1, cy, t * 2.f);
            drawList->AddRectFilled(ImVec2(x, cy), ImVec2(x + bar, end), color, bar * 0.5f);
        }
        else
        {
            const float end = Mix(cy, y0, (t - 0.5f) * 2.f);
            drawList->AddRectFilled(ImVec2(x, end), ImVec2(x + bar, cy), color, bar * 0.5f);
        }
        drawList->AddLine(ImVec2(x - 2.f, cy), ImVec2(x + bar + 2.f, cy), IM_COL32(255, 255, 255, 130), 1.f);
    };

    if(lastAxisPresent[AxisRoll])
    {
        const ImU32 rollColor = dangerColor(lastAxisValues[AxisRoll], rollBase);
        drawHorizontalAxis(topY, min.x + pad * 2.f, max.x - pad * 2.f, lastAxisValues[AxisRoll], rollColor, true, "Roll");
    }
    if(lastAxisPresent[AxisSurge])
    {
        const ImU32 surgeColor = dangerColor(lastAxisValues[AxisSurge], surgeBase);
        const float y0 = min.y + pad * 4.f;
        const float y1 = max.y - pad * 5.4f;
        drawVerticalAxis(min.x + pad, y0, y1, lastAxisValues[AxisSurge], surgeColor);
        auto txt = Util::Format("Surge %d", AxisPercent(lastAxisValues[AxisSurge]));
        drawTextCentered(ImVec2(min.x + pad + bar + fontSize * 2.2f, centerY), txt, surgeColor);
    }
    if(lastAxisPresent[AxisPitch])
    {
        const ImU32 pitchColor = dangerColor(lastAxisValues[AxisPitch], pitchBase);
        const float y0 = min.y + pad * 4.f;
        const float y1 = max.y - pad * 5.4f;
        drawVerticalAxis(max.x - pad - bar, y0, y1, lastAxisValues[AxisPitch], pitchColor);
        auto txt = Util::Format("Pitch %d", AxisPercent(lastAxisValues[AxisPitch]));
        drawTextCentered(ImVec2(max.x - pad - bar - fontSize * 1.8f, centerY), txt, pitchColor);
    }
    if(lastAxisPresent[AxisSway])
    {
        const ImU32 swayColor = dangerColor(lastAxisValues[AxisSway], swayBase);
        drawHorizontalAxis(bottomY, min.x + pad * 2.f, max.x - pad * 2.f, lastAxisValues[AxisSway], swayColor, false, "Sway", true);
        auto txt = Util::Format("Sway %d", AxisPercent(lastAxisValues[AxisSway]));
        const float sideX = lastAxisValues[AxisSway] < 0.5f ? max.x - pad * 5.f : min.x + pad * 5.f;
        drawTextCentered(ImVec2(sideX, bottomY - fontSize * 0.8f), txt, swayColor);
    }

    if(lastAxisPresent[AxisStroke])
    {
        auto txt = Util::Format("%d%%", static_cast<int>(std::round(lastStrokeTravelPercent)));
        float labelX = centerX;
        float labelY = max.y - pad * 5.1f;
        if(lastStrokeLineValid)
        {
            const float dx = lastStrokeAnchorTop.x - lastStrokeAnchorBottom.x;
            const float dy = lastStrokeAnchorTop.y - lastStrokeAnchorBottom.y;
            if((dx * dx + dy * dy) > 4.f)
            {
                drawList->AddLine(lastStrokeAnchorTop, lastStrokeAnchorBottom, IM_COL32(240, 240, 240, 180), std::max(1.f, 2.f * scale));
            }
        }
        if(lastStrokeBaseScreenBoundsValid)
        {
            labelX = (lastStrokeBaseScreenMin.x + lastStrokeBaseScreenMax.x) * 0.5f;
            labelY = lastStrokeBaseScreenMax.y + fontSize * 1.08f;
        }
        labelX = Util::Clamp<float>(labelX, min.x + fontSize * 2.2f, max.x - fontSize * 2.2f);
        labelY = Util::Clamp<float>(labelY, min.y + fontSize * 1.5f, max.y - fontSize * 1.5f);
        drawStrokeBadge(ImVec2(labelX, labelY), txt, lastStrokeTravelPercent / 100.f);
    }
}
};

