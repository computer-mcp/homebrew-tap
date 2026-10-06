cask "computer-mcp" do
  version "1.3.3"
  sha256 "7e2b268f65b17eccc0c327553f525e23609b042e0b6c406038eda0f22c4321fd"

  url "https://github.com/computer-mcp/computer-mcp/releases/download/v#{version}/Computer-MCP-#{version}-universal.dmg"
  name "Computer MCP"
  desc "Governed local MCP gateway for ChatGPT"
  homepage "https://github.com/computer-mcp/computer-mcp"

  depends_on macos: :sonoma

  app "Computer MCP.app"
end
