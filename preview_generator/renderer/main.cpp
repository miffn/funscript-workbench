// Standalone OFS-custom simulator + extracted ImGui HUD, offscreen EGL.
#include <glad/gl.h>
#include <EGL/egl.h>
#include <EGL/eglext.h>
#include "GltfLoader.h"
#include "json.hpp"
#include "motion.h"
#include "hud.h"
#include <imgui_impl_opengl3.h>
#include <sstream>
#include <glm/gtc/matrix_transform.hpp>
#include <algorithm>
#include <cmath>
#include <fstream>
#include <iostream>
#include <limits>
#include <map>
#include <stdexcept>
#include <string>
#include <vector>
constexpr float kEmbeddedCylinderRadius = .52f;
constexpr float kEmbeddedCylinderHalfHeight = .84f;
#include "cylinder.h"

namespace {
constexpr const char* Version = "ofs-preview-renderer/0.2.0";
class EglContext {
    EGLDisplay display = EGL_NO_DISPLAY;
    EGLSurface surface = EGL_NO_SURFACE;
    EGLContext context = EGL_NO_CONTEXT;
public:
    EglContext(int width, int height) {
        try {
            const auto getDisplay = reinterpret_cast<PFNEGLGETPLATFORMDISPLAYEXTPROC>(
                eglGetProcAddress("eglGetPlatformDisplayEXT"));
            if (!getDisplay) throw std::runtime_error("EGL platform extension unavailable");
            display = getDisplay(EGL_PLATFORM_SURFACELESS_MESA, EGL_DEFAULT_DISPLAY, nullptr);
            EGLint major, minor;
            if (display == EGL_NO_DISPLAY || !eglInitialize(display, &major, &minor))
                throw std::runtime_error("Cannot initialize surfaceless EGL; install Mesa EGL drivers");
            if (!eglBindAPI(EGL_OPENGL_API)) throw std::runtime_error("Cannot bind OpenGL API");
            const EGLint attrs[] = {EGL_SURFACE_TYPE,EGL_PBUFFER_BIT,EGL_RENDERABLE_TYPE,EGL_OPENGL_BIT,
                EGL_RED_SIZE,8,EGL_GREEN_SIZE,8,EGL_BLUE_SIZE,8,EGL_ALPHA_SIZE,8,EGL_DEPTH_SIZE,24,EGL_NONE};
            EGLConfig config; EGLint count;
            if (!eglChooseConfig(display,attrs,&config,1,&count) || count == 0)
                throw std::runtime_error("No RGBA/depth EGL pbuffer configuration");
            const EGLint surfaceAttrs[] = {EGL_WIDTH,width,EGL_HEIGHT,height,EGL_NONE};
            surface = eglCreatePbufferSurface(display,config,surfaceAttrs);
            const EGLint contextAttrs[] = {EGL_CONTEXT_MAJOR_VERSION,3,EGL_CONTEXT_MINOR_VERSION,3,
                EGL_CONTEXT_OPENGL_PROFILE_MASK,EGL_CONTEXT_OPENGL_CORE_PROFILE_BIT,EGL_NONE};
            context = eglCreateContext(display,config,EGL_NO_CONTEXT,contextAttrs);
            if (surface == EGL_NO_SURFACE || context == EGL_NO_CONTEXT ||
                !eglMakeCurrent(display,surface,surface,context))
                throw std::runtime_error("Cannot create OpenGL 3.3 offscreen context");
            if (!gladLoadGL(reinterpret_cast<GLADloadfunc>(eglGetProcAddress)))
                throw std::runtime_error("Cannot load OpenGL functions");
            std::cerr << Version << " | " << glGetString(GL_RENDERER) << " | " << glGetString(GL_VERSION) << '\n';
        } catch (...) { release(); throw; }
    }
    void release() noexcept {
        if (display == EGL_NO_DISPLAY) return;
        eglMakeCurrent(display,EGL_NO_SURFACE,EGL_NO_SURFACE,EGL_NO_CONTEXT);
        if (context != EGL_NO_CONTEXT) eglDestroyContext(display,context);
        if (surface != EGL_NO_SURFACE) eglDestroySurface(display,surface);
        eglTerminate(display); display = EGL_NO_DISPLAY;
    }
    ~EglContext() { release(); }
};

class Simulator {
    // GL resources are destroyed before EglContext leaves scope.
    ofs::sg::SceneGraph graph;
    ofs::sg::Mesh cylinder, marker, base;
    std::unique_ptr<ofs::sg::GltfScene> gltf;
    ofs::sg::SceneNode *control=nullptr, *anchor=nullptr;
    glm::vec3 bottom{0,-kEmbeddedCylinderHalfHeight,0};
    glm::vec3 boundsMin{-kEmbeddedCylinderRadius,-kEmbeddedCylinderHalfHeight,-kEmbeddedCylinderRadius};
    glm::vec3 boundsMax{kEmbeddedCylinderRadius,kEmbeddedCylinderHalfHeight,kEmbeddedCylinderRadius};
public:
    explicit Simulator(const std::string& model) {
        control=graph.createNode();
        if (!model.empty()) {
            gltf=ofs::sg::loadGltf(graph,model);
            if (!gltf || gltf->roots.empty() || gltf->meshes.empty())
                throw std::runtime_error("Cannot load selected model: " + model);
            for (auto* root : gltf->roots) graph.setParent(root,control);
            if (gltf->hasBounds) { boundsMin=gltf->boundsMin; boundsMax=gltf->boundsMax; }
            bottom={ (boundsMin.x+boundsMax.x)*.5f, boundsMin.y, (boundsMin.z+boundsMax.z)*.5f };
        } else {
            cylinder=CreateGodotCylinderMesh(); marker=ofs::sg::Mesh::cube();
            auto* body=graph.createNode(control); body->mesh=&cylinder;
            for (int side : {-1,1}) {
                auto* node=graph.createNode(body); node->mesh=&marker;
                node->localTransform.position={side*kEmbeddedCylinderRadius,0,0};
                node->localTransform.scale={.25f,.25f,.25f};
                node->color=side<0 ? glm::vec4{1,.02f,.01f,1} : glm::vec4{.50f,0,.95f,1};
            }
        }
        base=ofs::sg::Mesh::cube(); anchor=graph.createNode(); anchor->mesh=&base;
        anchor->color={.96f,.96f,.96f,.55f};
    }
    void render(const preview::Axes& values, float scale, float pitchRange,
                const glm::mat4& view, const glm::mat4& projection,
                AxisHud& hud, int width, int height) {
        const auto motion=preview::evaluate(values,pitchRange);
        control->localTransform.position=motion.position;
        control->localTransform.rotation=motion.rotation;
        control->localTransform.scale=glm::vec3{scale};
        const float modelHeight=std::max(.1f,boundsMax.y-boundsMin.y)*scale;
        const float size=std::clamp(modelHeight*.052f,.07f,.14f);
        const float gap=std::clamp(modelHeight*.025f,.035f,.065f);
        anchor->localTransform.position=glm::vec3{0,-1,0}+bottom*scale-glm::vec3{0,gap+size*.5f,0};
        anchor->localTransform.scale=glm::vec3{size};
        graph.updateTransforms(); graph.render(view,projection);
        hud.lastAxisValues=values;
        const auto modelBottom=glm::vec3(control->worldMatrix*glm::vec4(bottom,1));
        hud.lastStrokeTravelPercent=glm::length(modelBottom-(glm::vec3{0,-1,0}+bottom*scale))/2.f*100.f;
        auto project=[&](glm::vec3 point, ImVec2& pixel) {
            const auto clip=projection*view*glm::vec4(point,1);
            if(clip.w<=0) return false;
            pixel={ (clip.x/clip.w+1.f)*width*.5f, (1.f-clip.y/clip.w)*height*.5f };
            return true;
        };
        hud.lastStrokeLineValid=project(modelBottom,hud.lastStrokeAnchorTop)
            && project(glm::vec3(anchor->worldMatrix*glm::vec4(0,.5f,0,1)),hud.lastStrokeAnchorBottom);
        hud.lastStrokeBaseScreenMin={float(width),float(height)};
        hud.lastStrokeBaseScreenMax={0,0};
        hud.lastStrokeBaseScreenBoundsValid=true;
        for(float x:{-.5f,.5f}) for(float y:{-.5f,.5f}) for(float z:{-.5f,.5f}) {
            ImVec2 p;
            if(!project(glm::vec3(anchor->worldMatrix*glm::vec4(x,y,z,1)),p)) {
                hud.lastStrokeBaseScreenBoundsValid=false; continue;
            }
            hud.lastStrokeBaseScreenMin.x=std::min(hud.lastStrokeBaseScreenMin.x,p.x);
            hud.lastStrokeBaseScreenMin.y=std::min(hud.lastStrokeBaseScreenMin.y,p.y);
            hud.lastStrokeBaseScreenMax.x=std::max(hud.lastStrokeBaseScreenMax.x,p.x);
            hud.lastStrokeBaseScreenMax.y=std::max(hud.lastStrokeBaseScreenMax.y,p.y);
        }
    }
};
float number(const std::map<std::string,std::string>& args,const std::string& name,float fallback,float min,float max) {
    const auto it=args.find(name);
    if (it==args.end()) return fallback;
    size_t consumed=0;
    const float value=std::stof(it->second,&consumed);
    if (consumed!=it->second.size() || !std::isfinite(value) || value<min || value>max)
        throw std::runtime_error("Invalid argument " + name);
    return value;
}
std::string required(const std::map<std::string,std::string>& args,const std::string& name) {
    const auto it=args.find(name);
    if (it==args.end() || it->second.empty()) throw std::runtime_error("Required argument: " + name);
    return it->second;
}
}

int main(int argc,char** argv) {
    try {
        const std::vector<std::string> allowed={"--frames-json","--output","--width","--height","--model",
            "--pitch-range","--camera-yaw","--camera-pitch","--camera-distance","--camera-fov","--model-scale",
            "--axes-present","--hud-text-scale","--no-axis-hud"};
        std::map<std::string,std::string> args;
        for (int i=1;i<argc;++i) {
            const std::string key=argv[i];
            if(key=="--no-axis-hud") {
                if(args.count(key)) throw std::runtime_error("Repeated argument "+key);
                args[key]="true"; continue;
            }
            if (key=="--version") { std::cout << Version << '\n'; return 0; }
            if (key=="--help") {
                std::cout << "ofs-preview-renderer --frames-json frames.json --output simulator.rgba [--width 480 --height 480 --model model.glb --axes-present stroke,pitch --hud-text-scale 1.1 --no-axis-hud]\n"
                    << "frames.json is an array of [stroke,surge,sway,twist,roll,pitch], normalized 0..1.\n"
                    << "Output: tightly packed top-down straight-alpha RGBA8 frames, no header.\n"; return 0;
            }
            if (std::find(allowed.begin(),allowed.end(),key)==allowed.end() || i+1>=argc || args.count(key))
                throw std::runtime_error("Unknown, missing, or repeated argument " + key);
            args[key]=argv[++i];
        }
        const int width=static_cast<int>(number(args,"--width",480,16,4096));
        const int height=static_cast<int>(number(args,"--height",480,16,4096));
        const float scale=number(args,"--model-scale",.8f,.1f,5);
        const float pitchRange=number(args,"--pitch-range",60,0,90);
        const float yaw=glm::radians(number(args,"--camera-yaw",0,-180,180));
        const float pitch=glm::radians(number(args,"--camera-pitch",0,-180,180));
        const float distance=number(args,"--camera-distance",8.4f,1.5f,20);
        const float fov=glm::radians(number(args,"--camera-fov",45,15,80));
        AxisHud hud;
        hud.state.axisValueTextScale=number(args,"--hud-text-scale",1.1f,.7f,1.8f);
        hud.state.showAxisHud=!args.count("--no-axis-hud");
        if(args.count("--axes-present")) {
            std::istringstream names(args.at("--axes-present")); std::string name;
            const std::array<std::string,6> known={"stroke","surge","sway","twist","roll","pitch"};
            if(args.at("--axes-present").empty() || args.at("--axes-present").back()==',')
                throw std::runtime_error("Empty axis name");
            while(std::getline(names,name,',')) {
                auto found=std::find(known.begin(),known.end(),name);
                if(found==known.end()) throw std::runtime_error("Unknown axis "+name);
                auto index=std::distance(known.begin(),found);
                if(hud.lastAxisPresent[index]) throw std::runtime_error("Repeated axis "+name);
                hud.lastAxisPresent[index]=true;
            }
        }
        std::ifstream input(required(args,"--frames-json"));
        if (!input) throw std::runtime_error("Cannot open frames JSON");
        const auto frames=nlohmann::json::parse(input);
        if (!frames.is_array() || frames.empty() || frames.size()>100000)
            throw std::runtime_error("Expected 1..100000 frames");
        std::vector<preview::Axes> samples;
        for (const auto& row : frames) {
            if (!row.is_array() || row.size()!=6) throw std::runtime_error("Every frame must contain six axes");
            preview::Axes values;
            for (size_t axis=0;axis<6;++axis) {
                if (!row[axis].is_number()) throw std::runtime_error("Non-numeric axis position");
                values[axis]=row[axis].get<float>();
                if (!std::isfinite(values[axis]) || values[axis]<0 || values[axis]>1)
                    throw std::runtime_error("Axis position must be normalized 0..1");
            }
            samples.push_back(values);
        }
        const auto orientation=glm::angleAxis(yaw,glm::vec3{0,1,0})*glm::angleAxis(pitch,glm::vec3{1,0,0});
        const glm::mat4 view=glm::lookAt(orientation*glm::vec3{0,0,distance},glm::vec3{0},orientation*glm::vec3{0,1,0});
        const glm::mat4 projection=glm::perspective(fov,static_cast<float>(width)/height,.1f,100.f);
        EglContext context(width,height);
        ImGui::CreateContext();
        auto& io=ImGui::GetIO();
        io.IniFilename=nullptr; io.LogFilename=nullptr;
        io.DisplaySize={float(width),float(height)};
        io.DeltaTime=1.f/30.f;
        ImFontConfig font; font.SizePixels=18.f; io.Fonts->AddFontDefault(&font);
        if(!ImGui_ImplOpenGL3_Init("#version 330")) throw std::runtime_error("Cannot initialize ImGui renderer");
        Simulator simulator(args.count("--model") ? args.at("--model") : "");
        std::ofstream output(required(args,"--output"),std::ios::binary);
        if (!output) throw std::runtime_error("Cannot open RGBA output");
        glViewport(0,0,width,height); glPixelStorei(GL_PACK_ALIGNMENT,1);
        std::vector<unsigned char> pixels(static_cast<size_t>(width)*height*4);
        glClearColor(0,0,0,0);
        for (size_t index=0;index<samples.size();++index) {
            glClear(GL_COLOR_BUFFER_BIT|GL_DEPTH_BUFFER_BIT);
            ImGui_ImplOpenGL3_NewFrame(); ImGui::NewFrame();
            glEnable(GL_BLEND);
            glBlendFuncSeparate(GL_SRC_ALPHA,GL_ONE_MINUS_SRC_ALPHA,GL_ONE,GL_ONE_MINUS_SRC_ALPHA);
            simulator.render(samples[index],scale,pitchRange,view,projection,hud,width,height);
            hud.draw(ImGui::GetForegroundDrawList(),{0,0},{float(width),float(height)});
            ImGui::Render(); ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());
            glReadPixels(0,0,width,height,GL_RGBA,GL_UNSIGNED_BYTE,pixels.data());
            const auto error=glGetError();
            if (error!=GL_NO_ERROR) throw std::runtime_error("OpenGL rendering error " + std::to_string(error));
            for(size_t p=0;p<pixels.size();p+=4) {
                const unsigned alpha=pixels[p+3];
                if(alpha>0 && alpha<255)
                    for(int c=0;c<3;++c) pixels[p+c]=std::min(255u,(unsigned(pixels[p+c])*255u+alpha/2)/alpha);
            }
            // OpenGL starts at bottom-left; FFmpeg rawvideo starts at top-left.
            for (int y=height-1;y>=0;--y)
                output.write(reinterpret_cast<char*>(pixels.data()+static_cast<size_t>(y)*width*4),width*4);
            if (!output) throw std::runtime_error("Cannot write RGBA frame (disk full?)");
            if (index%30==0 || index+1==samples.size())
                std::cerr << "rendered " << index+1 << '/' << samples.size() << '\n';
        }
        output.flush();
        if (!output) throw std::runtime_error("Cannot flush RGBA output");
        ImGui_ImplOpenGL3_Shutdown(); ImGui::DestroyContext();
        return 0;
    } catch (const std::exception& error) {
        std::cerr << "renderer error: " << error.what() << '\n'; return 1;
    }
}
