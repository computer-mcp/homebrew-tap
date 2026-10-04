class AppleCli < Formula
  desc "Automate Apple apps using CLI and MCP"
  homepage "https://github.com/computer-mcp/apple-cli"
  url "https://github.com/computer-mcp/apple-cli/releases/download/v0.1.0-alpha.3/apple-cli-0.1.0-alpha.3-macos-arm64.tar.gz"
  sha256 "b7be69f407be64c512d1ed29aa3b796329d19b6e33aa3786966a1957f7c0cc32"
  license "Apache-2.0"

  depends_on arch: :arm64
  depends_on macos: :golden_gate

  # Preserve the accepted executables and their bundled runtime signatures.
  skip_clean "libexec"

  def install
    libexec.install Dir["*"]
    bin.install_symlink libexec/"bin/apple", libexec/"bin/apple-cli-mcp"
  end

  test do
    assert_equal version.to_s, shell_output("#{bin}/apple --version").strip
    assert_equal version.to_s, shell_output("#{bin}/apple-cli-mcp --version").strip
    assert_match "notes", shell_output("#{bin}/apple --help")
    preview = JSON.parse(shell_output("#{bin}/apple notifications preview --title Homebrew --body test --json"))
    assert_equal true, preview["ok"]
    assert_equal false, preview.dig("data", "externalAction")
  end
end
