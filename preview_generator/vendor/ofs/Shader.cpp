#include "Shader.h"
#include <stdexcept>
#include "OFS_Util.h"
#include <glad/gl.h>

namespace ofs {
Shader::Shader(const char *vertexSource, const char *fragmentSource) {
    uint32_t vertex = 0, fragment = 0;

    vertex = glCreateShader(GL_VERTEX_SHADER);
    glShaderSource(vertex, 1, &vertexSource, nullptr);
    glCompileShader(vertex);
    checkCompileErrors(vertex, "VERTEX", false);

    fragment = glCreateShader(GL_FRAGMENT_SHADER);
    glShaderSource(fragment, 1, &fragmentSource, nullptr);
    glCompileShader(fragment);
    checkCompileErrors(fragment, "FRAGMENT", false);

    program = glCreateProgram();
    glAttachShader(program, vertex);
    glAttachShader(program, fragment);
    glLinkProgram(program);
    checkCompileErrors(program, "PROGRAM", true);

    glDeleteShader(vertex);
    glDeleteShader(fragment);
}

Shader::~Shader() {
    if (program != 0) {
        glDeleteProgram(program);
    }
}

void Shader::use() const {
    if (program == 0) // not compiled (headless, or a compile/link failure); only used from draw callbacks
        return;
    glUseProgram(program);
}

void Shader::checkCompileErrors(uint32_t object, const char *label, bool isProgram) {
    int32_t success = 0;
    if (isProgram)
        glGetProgramiv(object, GL_LINK_STATUS, &success);
    else
        glGetShaderiv(object, GL_COMPILE_STATUS, &success);
    if (success)
        return;

    char infoLog[1024];
    if (isProgram)
        glGetProgramInfoLog(object, sizeof(infoLog), nullptr, infoLog);
    else
        glGetShaderInfoLog(object, sizeof(infoLog), nullptr, infoLog);
    LOGF_ERROR("%s of type: %s\n%s\n-- --------------------------------------------------- --",
        isProgram ? "PROGRAM_LINKING_ERROR" : "SHADER_COMPILATION_ERROR", label, infoLog);
    throw std::runtime_error("OFS shader compilation/linking failed");
}
} // namespace ofs

