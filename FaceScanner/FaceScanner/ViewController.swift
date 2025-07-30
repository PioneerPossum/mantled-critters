import UIKit
import ARKit
import SceneKit
import ModelIO
import MetalKit

class ViewController: UIViewController, ARSessionDelegate, ARSCNViewDelegate {

    // MARK: - IBOutlets
    @IBOutlet weak var sceneView: ARSCNView!
    @IBOutlet weak var exportOBJButton: UIButton!
    @IBOutlet weak var exportSTLButton: UIButton!

    // Hold latest face anchor
    private var currentFaceAnchor: ARFaceAnchor?

    override func viewDidLoad() {
        super.viewDidLoad()

        // 1. Setup ARSCNView
        sceneView.delegate = self
        sceneView.session.delegate = self
        sceneView.automaticallyUpdatesLighting = true
        sceneView.scene = SCNScene()

        // 2. Style buttons
        [exportOBJButton, exportSTLButton].forEach { btn in
            btn?.layer.cornerRadius = 8
            btn?.backgroundColor = UIColor(white: 0.1, alpha: 0.6)
            btn?.setTitleColor(.white, for: .normal)
        }
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)

        guard ARFaceTrackingConfiguration.isSupported else {
            fatalError("Requires a TrueDepth-capable device.")
        }

        let config = ARFaceTrackingConfiguration()
        config.isLightEstimationEnabled = true
        sceneView.session.run(config,
                              options: [.resetTracking, .removeExistingAnchors])
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        sceneView.session.pause()
    }

    // MARK: - ARSessionDelegate

    func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        for a in anchors {
            if let face = a as? ARFaceAnchor {
                currentFaceAnchor = face
            }
        }
    }

    // MARK: - IBActions

    @IBAction func exportOBJ(_ sender: UIButton) {
        exportMesh(as: "obj")
    }

    @IBAction func exportSTL(_ sender: UIButton) {
        exportMesh(as: "stl")
    }

    // MARK: - Mesh Export

    private func exportMesh(as ext: String) {
        guard let face = currentFaceAnchor else {
            showAlert("Still scanning… move your head slowly.")
            return
        }
        let geometry = face.geometry

        // 1. Build vertex & index data
        let verts = geometry.vertices
        let inds = geometry.triangleIndices

        let vCount = verts.count
        let iCount = inds.count

        let vertexData = Data(bytes: verts,
                              count: MemoryLayout<vector_float3>.stride * vCount)
        let indexData  = Data(bytes: inds,
                              count: MemoryLayout<UInt16>.stride * iCount)

        // 2. ModelIO buffers
        let device = MTLCreateSystemDefaultDevice()!
        let allocator = MTKMeshBufferAllocator(device: device)
        let vbuf = allocator.newBuffer(with: vertexData, type: .vertex)
        let ibuf = allocator.newBuffer(with: indexData, type: .index)

        // 3. Describe layout
        let vdesc = MDLVertexDescriptor()
        vdesc.attributes[0] = MDLVertexAttribute(
            name: MDLVertexAttributePosition,
            format: .float3,
            offset: 0, bufferIndex: 0)
        vdesc.layouts[0] = MDLVertexBufferLayout(stride: MemoryLayout<vector_float3>.stride)

        // 4. Submesh & MDLMesh
        let triCount = iCount / 3
        let submesh = MDLSubmesh(indexBuffer: ibuf,
                                 indexCount: iCount,
                                 indexType: .uInt16,
                                 geometryType: .triangles,
                                 material: nil)
        let mesh = MDLMesh(vertexBuffer: vbuf,
                           vertexCount: vCount,
                           descriptor: vdesc,
                           submeshes: [submesh])

        // 5. Export asset
        let asset = MDLAsset()
        asset.add(mesh)

        let filename = "faceScan.\(ext)"
        let tmpURL = FileManager.default.temporaryDirectory.appendingPathComponent(filename)

        do {
            try asset.export(to: tmpURL)
            presentShareSheet(for: tmpURL)
        } catch {
            showAlert("Export failed: \(error.localizedDescription)")
        }
    }

    // MARK: - Sharing

    private func presentShareSheet(for fileURL: URL) {
        let share = UIActivityViewController(activityItems: [fileURL], applicationActivities: nil)
        share.popoverPresentationController?.sourceView = self.view
        present(share, animated: true)
    }

    // MARK: - Helpers

    private func showAlert(_ msg: String) {
        let a = UIAlertController(title: "FaceScanner", message: msg, preferredStyle: .alert)
        a.addAction(.init(title: "OK", style: .default))
        present(a, animated: true)
    }

    // MARK: - SCN Delegate (to visualize)

    func renderer(_ renderer: SCNSceneRenderer, nodeFor anchor: ARAnchor) -> SCNNode? {
        guard anchor is ARFaceAnchor else { return nil }
        let node = SCNNode()
        let faceGeo = ARSCNFaceGeometry(device: sceneView.device!)!
        node.geometry = faceGeo
        return node
    }

    func renderer(_ renderer: SCNSceneRenderer,
                  didUpdate node: SCNNode,
                  for anchor: ARAnchor) {
        guard let fa = anchor as? ARFaceAnchor,
              let geo = node.geometry as? ARSCNFaceGeometry else { return }
        geo.update(from: fa.geometry)
    }
}
