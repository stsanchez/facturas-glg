const dropArea = document.getElementById('drop-area');
const fileElem = document.getElementById('fileElem');
const progressBar = document.getElementById('progressBar');
const fileList = document.getElementById('fileList');

['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
    dropArea.addEventListener(eventName, preventDefaults, false)
})

function preventDefaults (e) {
    e.preventDefault()
    e.stopPropagation()
}

dropArea.addEventListener('drop', handleDrop, false)

function handleDrop(e) {
    let dt = e.dataTransfer
    let files = dt.files

    handleFiles(files)
}

fileElem.addEventListener('change', (e) => {
    handleFiles(e.target.files);
});

dropArea.addEventListener('click', () => {
    fileElem.click();
});


async function handleFiles(files) {
    progressBar.removeAttribute('hidden');
    progressBar.value = 0;
    fileList.innerHTML = ""; // Limpiar la lista de archivos

    const formData = new FormData();

    for (let i = 0; i < files.length; i++) {
        const file = files[i];
        formData.append('file', file); // 'file' debe coincidir con el nombre en request.files en Flask
        fileList.innerHTML += `<p>${file.name} - Cargando...</p>`; // Mostrar estado de carga
    }

    try {
        const response = await fetch('/', { // Enviar a la raíz de tu aplicación Flask
            method: 'POST',
            body: formData
        });

        if (response.ok) {
            console.log('Archivos subidos correctamente');
            fileList.innerHTML = `<p style="color: green;">Archivos procesados correctamente.</p>`;
            setTimeout(() => {
                window.location.reload(); // Recarga la página para mostrar los resultados
            }, 2000); // Espera 2 segundos antes de recargar
        } else {
            console.error('Error al subir archivos:', response.status);
            const errorText = await response.text(); // Obtener el mensaje de error del servidor
            fileList.innerHTML = `<p style="color: red;">Error: ${errorText}</p>`; // Mostrar el error
            progressBar.setAttribute('hidden', true);
        }
    } catch (error) {
        console.error('Error de red:', error);
        fileList.innerHTML = `<p style="color: red;">Error de red</p>`;
        progressBar.setAttribute('hidden', true);
    } finally {
        progressBar.setAttribute('hidden', true);
    }
}