import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

/** Run the Python PDF extraction example from Java. */
public class TableExtractionExample {
    public static void main(String[] args) throws Exception {
        if (args.length > 2) {
            System.err.println(
                "Usage: TableExtractionExample [pdf-path] [output-directory]"
            );
            System.exit(2);
        }

        Path projectRoot = Path.of("").toAbsolutePath().normalize();
        boolean windows = System.getProperty("os.name")
            .toLowerCase()
            .contains("win");
        Path python = projectRoot.resolve(
            windows ? ".venv/Scripts/python.exe" : ".venv/bin/python"
        );
        Path script = projectRoot.resolve("examples/extract_and_chunk_pdf.py");
        Path pdf = projectRoot.resolve(
            args.length >= 1 ? args[0] : "tests/artifacts/Q8.pdf"
        ).normalize();
        Path outputDirectory = projectRoot.resolve(
            args.length == 2 ? args[1] : "example_output"
        ).normalize();

        if (!Files.isRegularFile(python)) {
            throw new IllegalStateException(
                "Python virtual environment not found at " + python
            );
        }
        if (!Files.isRegularFile(script)) {
            throw new IllegalStateException("Python example not found at " + script);
        }
        if (!Files.isRegularFile(pdf)) {
            throw new IllegalArgumentException("PDF not found at " + pdf);
        }

        ProcessBuilder processBuilder = new ProcessBuilder(
            List.of(
                python.toString(),
                script.toString(),
                pdf.toString(),
                "--output-directory",
                outputDirectory.toString()
            )
        );
        processBuilder.directory(projectRoot.toFile());
        processBuilder.redirectErrorStream(true);

        Process process = processBuilder.start();
        try (
            BufferedReader reader = new BufferedReader(
                new InputStreamReader(
                    process.getInputStream(),
                    StandardCharsets.UTF_8
                )
            )
        ) {
            String line;
            while ((line = reader.readLine()) != null) {
                System.out.println("[Python] " + line);
            }
        }

        int exitCode = process.waitFor();
        if (exitCode != 0) {
            throw new IllegalStateException(
                "PDF extraction failed with exit code " + exitCode
            );
        }
    }
}
